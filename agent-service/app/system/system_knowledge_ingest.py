"""Materializa entidades de negocio de SolidSET como conocimiento RAG auditable."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import psycopg
from langchain_ollama import OllamaEmbeddings
from psycopg.rows import dict_row
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList, PointStruct

from app.config import settings
from app.connectors.solidset_sql import connect as connect_solidset_sql
from app.rag.vector_store import ensure_vector_collection


# Tablas de negocio visibles en docs/BD-SolidSet.png. SysChat y archivos se
# excluyen: tienen pipelines con permisos propios y/o contenido potencialmente sensible.
DEFAULT_BUSINESS_TABLES = (
    "Activity", "Activity2Channel", "SysTask", "SysTask2Channel",
    "SysTaskResourceRole", "SysActivityResourceRoleActivity",
    "SysWorkRoom", "SysWorkRoomResource", "SysResources",
    "SysLogin2SysResource", "SysLogin", "SysCommunity",
    "SysCommunity2Resource", "SysCommunity2Company",
    "SysCompany2Workroom", "SysCompany2Login", "Entity_SysCompany",
    "Entity_SysPerson", "SysPerson",
)

TABLE_PURPOSES = {
    "Activity": "actividades, planificación, estado, responsables y fechas",
    "SysTask": "tareas, asignaciones, estado, progreso, prioridad y fechas",
    "SysResources": "recursos del sistema, identidad operativa, estado y empresa",
    "SysLogin": "usuarios y nombres de acceso asociados a personas y recursos",
    "SysWorkRoom": "canales o salas de trabajo del sistema",
    "SysCommunity": "comunidades y agrupaciones organizativas",
    "Entity_SysCompany": "empresas y organizaciones",
    "Entity_SysPerson": "personas vinculadas con recursos y logins",
    "SysPerson": "personas vinculadas con recursos y logins",
}

SENSITIVE_COLUMN = re.compile(
    r"password|passwd|pwd|secret|token|api.?key|private.?key|salt|hash|"
    r"photo|image|binary|filecontent|certificate|credential",
    re.IGNORECASE,
)
TEXT_LABELS = {
    "displayname": "nombre visible", "fullname": "nombre completo",
    "shortname": "nombre corto", "subject": "asunto", "description": "descripción",
    "status": "estado", "workstatus": "estado de trabajo",
    "progresspercentage": "porcentaje de progreso", "priority": "prioridad",
    "startdate": "fecha de inicio", "enddate": "fecha de fin",
    "creationdate": "fecha de creación", "createdtime": "fecha de creación",
    "modifieddate": "fecha de modificación", "modifiedtime": "fecha de modificación",
    "username": "usuario", "code": "código", "name": "nombre",
    "iscompanyowner": "es propietario de empresa", "active": "activo",
}


def _pg_connection() -> psycopg.Connection:
    return psycopg.connect(
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB, row_factory=dict_row,
    )


def ensure_system_knowledge_schema() -> None:
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''
        CREATE TABLE IF NOT EXISTS public."SysAgentIASystemIngestionRun" (
          "ID" uuid PRIMARY KEY, "IDSolidSETInstance" uuid NOT NULL,
          "InstanceCode" varchar(100), "Status" varchar(30) NOT NULL,
          "CurrentTable" varchar(255), "TablesTotal" integer NOT NULL DEFAULT 0,
          "TablesCompleted" integer NOT NULL DEFAULT 0,
          "RowsRead" bigint NOT NULL DEFAULT 0, "RowsIndexed" bigint NOT NULL DEFAULT 0,
          "RowsSkipped" bigint NOT NULL DEFAULT 0, "Error" text,
          "StartedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          "CompletedAt" timestamptz, "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS "IX_SystemIngestionRun_Instance"
          ON public."SysAgentIASystemIngestionRun" ("IDSolidSETInstance", "StartedAt" DESC);
        CREATE TABLE IF NOT EXISTS public."SysAgentIASystemDocument" (
          "DocumentID" uuid PRIMARY KEY, "IDSolidSETInstance" uuid NOT NULL,
          "SourceTable" varchar(255) NOT NULL, "SourceRecordID" varchar(500) NOT NULL,
          "ContentHash" varchar(64) NOT NULL, "QdrantPointID" uuid NOT NULL UNIQUE,
          "Status" varchar(30) NOT NULL DEFAULT 'indexed',
          "IndexedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          "LastSeenRunID" uuid, "DeletedAt" timestamptz,
          UNIQUE ("IDSolidSETInstance", "SourceTable", "SourceRecordID")
        );
        CREATE INDEX IF NOT EXISTS "IX_SystemDocument_Source"
          ON public."SysAgentIASystemDocument" ("IDSolidSETInstance", "SourceTable", "Status");
        ''')


def _run_update(run_id: str, **values: Any) -> None:
    allowed = {
        "Status", "CurrentTable", "TablesTotal", "TablesCompleted",
        "RowsRead", "RowsIndexed", "RowsSkipped", "Error", "CompletedAt",
    }
    assignments, params = [], []
    for key, value in values.items():
        if key in allowed:
            assignments.append(f'"{key}"=%s')
            params.append(value)
    if not assignments:
        return
    params.append(uuid.UUID(run_id))
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE public."SysAgentIASystemIngestionRun" SET {", ".join(assignments)}, '
            '"UpdatedAt"=CURRENT_TIMESTAMP WHERE "ID"=%s', params,
        )


def create_run(instance: dict[str, Any]) -> str:
    ensure_system_knowledge_schema()
    instance_id = uuid.UUID(str(instance["ID"]))
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT "ID" FROM public."SysAgentIASystemIngestionRun"
          WHERE "IDSolidSETInstance"=%s AND "Status" IN ('queued','running') LIMIT 1''',
          (instance_id,))
        active = cur.fetchone()
        if active:
            return str(active["ID"])
        run_id = uuid.uuid4()
        cur.execute('''INSERT INTO public."SysAgentIASystemIngestionRun"
          ("ID","IDSolidSETInstance","InstanceCode","Status") VALUES (%s,%s,%s,'queued')''',
          (run_id, instance_id, str(instance.get("Code") or "")))
        return str(run_id)


def get_run_status(run_id: str | None = None, instance_id: str | None = None) -> dict[str, Any] | None:
    ensure_system_knowledge_schema()
    with _pg_connection() as conn, conn.cursor() as cur:
        if run_id:
            cur.execute('SELECT * FROM public."SysAgentIASystemIngestionRun" WHERE "ID"=%s',
                        (uuid.UUID(run_id),))
        elif instance_id:
            cur.execute('''SELECT * FROM public."SysAgentIASystemIngestionRun"
              WHERE "IDSolidSETInstance"=%s ORDER BY "StartedAt" DESC LIMIT 1''',
              (uuid.UUID(instance_id),))
        else:
            return None
        row = cur.fetchone()
        if not row:
            return None
        result = dict(row)
        for key, value in list(result.items()):
            if isinstance(value, (uuid.UUID, datetime)):
                result[key] = str(value)
        total = int(result.get("TablesTotal") or 0)
        completed = int(result.get("TablesCompleted") or 0)
        result["ProgressPercentage"] = round((completed / total) * 100, 2) if total else 0.0
        result["Complete"] = result.get("Status") == "completed"
        return result


def _catalog(
    connection: Any, requested_tables: Iterable[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
    requested = list(dict.fromkeys(str(value) for value in requested_tables if value))
    if not requested:
        return {}, {}
    placeholders = ",".join("%s" for _ in requested)
    with connection.cursor(as_dict=True) as cur:
        cur.execute(f'''SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION
          FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo'
            AND TABLE_NAME IN ({placeholders})
          ORDER BY TABLE_NAME, ORDINAL_POSITION''', tuple(requested))
        columns: dict[str, list[dict[str, Any]]] = {}
        for row in cur.fetchall() or []:
            columns.setdefault(str(row["TABLE_NAME"]), []).append(dict(row))
        cur.execute(f'''SELECT ku.TABLE_NAME, ku.COLUMN_NAME
          FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
          JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
            ON ku.CONSTRAINT_NAME=tc.CONSTRAINT_NAME AND ku.TABLE_SCHEMA=tc.TABLE_SCHEMA
          WHERE tc.TABLE_SCHEMA='dbo' AND tc.CONSTRAINT_TYPE='PRIMARY KEY'
            AND ku.TABLE_NAME IN ({placeholders})
          ORDER BY ku.TABLE_NAME, ku.ORDINAL_POSITION''', tuple(requested))
        keys: dict[str, list[str]] = {}
        for row in cur.fetchall() or []:
            keys.setdefault(str(row["TABLE_NAME"]), []).append(str(row["COLUMN_NAME"]))
        return columns, keys


def _safe_columns(columns: Iterable[dict[str, Any]]) -> list[str]:
    allowed_types = {
        "uniqueidentifier", "varchar", "nvarchar", "char", "nchar", "text", "ntext",
        "tinyint", "smallint", "int", "bigint", "bit", "decimal", "numeric", "float",
        "real", "date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "time",
    }
    return [
        str(item["COLUMN_NAME"]) for item in columns
        if str(item.get("DATA_TYPE") or "").lower() in allowed_types
        and not SENSITIVE_COLUMN.search(str(item["COLUMN_NAME"]))
    ]


def _quote(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"Identificador SQL inválido: {identifier}")
    return f"[{identifier}]"


def _record_id(row: dict[str, Any], primary_keys: list[str]) -> str:
    keys = primary_keys or list(row)[:1]
    value = "|".join(str(row.get(key) or "") for key in keys)
    return value or hashlib.sha256(json.dumps(row, default=str, sort_keys=True).encode()).hexdigest()


def _related_resources(row: dict[str, Any]) -> list[str]:
    values = []
    for key, value in row.items():
        if "resource" not in key.lower() or not value:
            continue
        try:
            values.append(str(uuid.UUID(str(value))))
        except (ValueError, TypeError, AttributeError):
            continue
    return list(dict.fromkeys(values))


def _semantic_text(table: str, row: dict[str, Any]) -> str:
    purpose = TABLE_PURPOSES.get(table, f"registros de negocio de {table}")
    facts = []
    for key, value in row.items():
        if value is None or str(value).strip() == "":
            continue
        label = TEXT_LABELS.get(key.lower(), re.sub(r"(?<!^)(?=[A-Z])", " ", key).lower())
        rendered = str(value).strip()
        if len(rendered) > 1000:
            rendered = rendered[:1000]
        facts.append(f"{label}: {rendered}")
    return f"Entidad SolidSET {table}. Contiene {purpose}. " + ". ".join(facts) + "."


def _upsert_documents(documents: list[dict[str, Any]], run_id: str) -> int:
    if not documents:
        return 0
    embeddings = OllamaEmbeddings(base_url=settings.EMBEDDING_BASE_URL, model=settings.EMBEDDING_MODEL_NAME)
    qdrant = QdrantClient(url=settings.VECTOR_DB_URL)
    ensure_vector_collection(qdrant, settings.VECTOR_COLLECTION_NAME, embeddings)
    vectors = embeddings.embed_documents([item["text"] for item in documents])
    qdrant.upsert(
        collection_name=settings.VECTOR_COLLECTION_NAME,
        points=[PointStruct(id=item["point_id"], vector=vector, payload=item["payload"])
                for item, vector in zip(documents, vectors)], wait=True,
    )
    with _pg_connection() as conn, conn.cursor() as cur:
        for item in documents:
            cur.execute('''INSERT INTO public."SysAgentIASystemDocument"
              ("DocumentID","IDSolidSETInstance","SourceTable","SourceRecordID",
               "ContentHash","QdrantPointID","LastSeenRunID")
              VALUES (%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT ("IDSolidSETInstance","SourceTable","SourceRecordID") DO UPDATE SET
               "ContentHash"=EXCLUDED."ContentHash","QdrantPointID"=EXCLUDED."QdrantPointID",
               "LastSeenRunID"=EXCLUDED."LastSeenRunID","Status"='indexed',
               "DeletedAt"=NULL,"IndexedAt"=CURRENT_TIMESTAMP''',
              (uuid.UUID(item["point_id"]), uuid.UUID(item["instance_id"]), item["table"],
               item["record_id"], item["content_hash"], uuid.UUID(item["point_id"]), uuid.UUID(run_id)))
    return len(documents)


def _documents_requiring_embedding(
    documents: list[dict[str, Any]], run_id: str,
) -> tuple[list[dict[str, Any]], int]:
    """Marca documentos vistos y devuelve solo nuevos o modificados."""
    if not documents:
        return [], 0
    changed: list[dict[str, Any]] = []
    unchanged = 0
    with _pg_connection() as conn, conn.cursor() as cur:
        for item in documents:
            cur.execute('''SELECT "ContentHash" FROM public."SysAgentIASystemDocument"
              WHERE "IDSolidSETInstance"=%s AND "SourceTable"=%s AND "SourceRecordID"=%s
                AND "Status"='indexed' ''',
              (uuid.UUID(item["instance_id"]), item["table"], item["record_id"]))
            existing = cur.fetchone()
            if existing and existing["ContentHash"] == item["content_hash"]:
                cur.execute('''UPDATE public."SysAgentIASystemDocument"
                  SET "LastSeenRunID"=%s,"DeletedAt"=NULL,"Status"='indexed'
                  WHERE "IDSolidSETInstance"=%s AND "SourceTable"=%s AND "SourceRecordID"=%s''',
                  (uuid.UUID(run_id), uuid.UUID(item["instance_id"]), item["table"], item["record_id"]))
                unchanged += 1
            else:
                changed.append(item)
    return changed, unchanged


def _retire_missing_documents(instance_id: str, table: str, run_id: str) -> int:
    """Retira de Qdrant registros que ya no aparecieron en una carga completa."""
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIASystemDocument"
          SET "Status"='deleted',"DeletedAt"=CURRENT_TIMESTAMP
          WHERE "IDSolidSETInstance"=%s AND "SourceTable"=%s AND "Status"='indexed'
            AND ("LastSeenRunID" IS NULL OR "LastSeenRunID"<>%s)
          RETURNING "QdrantPointID"''',
          (uuid.UUID(instance_id), table, uuid.UUID(run_id)))
        points = [str(row["QdrantPointID"]) for row in cur.fetchall() or []]
    if points:
        QdrantClient(url=settings.VECTOR_DB_URL).delete(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            points_selector=PointIdsList(points=points), wait=True,
        )
    return len(points)


def run_system_knowledge_ingestion(
    run_id: str, instance: dict[str, Any], requested_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Ejecuta una fotografía semántica incremental de datos de negocio."""
    read = indexed = skipped = completed = 0
    try:
        _run_update(run_id, Status="running")
        with connect_solidset_sql(instance, as_dict=True) as connection:
            requested = requested_tables or list(DEFAULT_BUSINESS_TABLES)
            catalog, primary_keys = _catalog(connection, requested)
            lookup = {name.lower(): name for name in catalog}
            missing = [name for name in requested if name.lower() not in lookup]
            if requested_tables and missing:
                raise ValueError(f"Tablas inexistentes o no accesibles: {', '.join(missing)}")
            tables = [lookup[name.lower()] for name in requested if name.lower() in lookup]
            _run_update(run_id, TablesTotal=len(tables))
            for table in tables:
                _run_update(run_id, CurrentTable=table)
                columns = _safe_columns(catalog[table])
                if not columns:
                    skipped += 1; completed += 1
                    _run_update(run_id, TablesCompleted=completed, RowsSkipped=skipped)
                    continue
                schema_text = (
                    f"Catálogo de la entidad SolidSET {table}. Finalidad: "
                    f"{TABLE_PURPOSES.get(table, f'datos de negocio de {table}')}. "
                    f"Columnas disponibles: {', '.join(columns)}. "
                    f"Clave primaria: {', '.join(primary_keys.get(table) or ['no declarada'])}."
                )
                schema_digest = hashlib.sha256(schema_text.encode("utf-8")).hexdigest()
                schema_point_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL, f"solidset-system:{instance['ID']}:{table}:__schema__",
                ))
                schema_document = {
                    "point_id": schema_point_id, "instance_id": str(instance["ID"]),
                    "table": table, "record_id": "__schema__", "content_hash": schema_digest,
                    "text": schema_text,
                    "payload": {
                        "page_content": schema_text, "source": "solidset_system_snapshot",
                        "document_type": "system_schema", "scope": "system_snapshot",
                        "solidset_instance_id": str(instance["ID"]), "source_table": table,
                        "source_record_id": "__schema__", "related_resource_ids": [],
                        "content_hash": schema_digest,
                        "indexed_at": datetime.now(timezone.utc).isoformat(),
                        "generated_by_ia": False,
                    },
                }
                changed_schema, unchanged_schema = _documents_requiring_embedding(
                    [schema_document], run_id,
                )
                skipped += unchanged_schema
                indexed += _upsert_documents(changed_schema, run_id)
                select_list = ", ".join(_quote(column) for column in columns)
                order_columns = primary_keys.get(table) or columns[:1]
                order_by = ", ".join(_quote(column) for column in order_columns)
                page_offset = 0
                page_size = min(500, max(50, int(settings.SYSTEM_KNOWLEDGE_BATCH_SIZE)))
                while True:
                    with connection.cursor(as_dict=True) as cur:
                        cur.execute(
                            f"SELECT {select_list} FROM dbo.{_quote(table)} WITH (NOLOCK) "
                            f"ORDER BY {order_by} OFFSET %s ROWS FETCH NEXT %s ROWS ONLY",
                            (page_offset, page_size),
                        )
                        rows = cur.fetchall() or []
                    if not rows:
                        break
                    documents = []
                    for raw in rows:
                        row = dict(raw)
                        record_id = _record_id(row, primary_keys.get(table, []))
                        text = _semantic_text(table, row)
                        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                        point_id = str(uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"solidset-system:{instance['ID']}:{table}:{record_id}",
                        ))
                        documents.append({
                            "point_id": point_id, "instance_id": str(instance["ID"]),
                            "table": table, "record_id": record_id, "content_hash": digest,
                            "text": text,
                            "payload": {
                                "page_content": text, "source": "solidset_system_snapshot",
                                "document_type": "system_entity", "scope": "system_snapshot",
                                "solidset_instance_id": str(instance["ID"]), "source_table": table,
                                "source_record_id": record_id, "related_resource_ids": _related_resources(row),
                                "content_hash": digest, "indexed_at": datetime.now(timezone.utc).isoformat(),
                                "generated_by_ia": False,
                            },
                        })
                    read += len(rows)
                    changed, unchanged = _documents_requiring_embedding(documents, run_id)
                    skipped += unchanged
                    for offset in range(0, len(changed), 100):
                        indexed += _upsert_documents(changed[offset:offset + 100], run_id)
                    _run_update(run_id, RowsRead=read, RowsIndexed=indexed, RowsSkipped=skipped)
                    page_offset += len(rows)
                    if len(rows) < page_size:
                        break
                skipped += _retire_missing_documents(str(instance["ID"]), table, run_id)
                completed += 1
                _run_update(run_id, TablesCompleted=completed, RowsRead=read, RowsIndexed=indexed)
        _run_update(
            run_id, Status="completed", CurrentTable=None, TablesCompleted=completed,
            RowsRead=read, RowsIndexed=indexed, RowsSkipped=skipped,
            CompletedAt=datetime.now(timezone.utc),
        )
        return get_run_status(run_id=run_id) or {}
    except Exception as exc:
        _run_update(
            run_id, Status="failed", Error=str(exc)[:4000], RowsRead=read,
            RowsIndexed=indexed, RowsSkipped=skipped, TablesCompleted=completed,
            CompletedAt=datetime.now(timezone.utc),
        )
        raise
