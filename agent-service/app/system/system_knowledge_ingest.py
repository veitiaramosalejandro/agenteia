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
from psycopg.types.json import Jsonb
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList, PointStruct

from app.config import settings
from app.interactive_priority import wait_for_interactive_idle
from app.connectors.solidset_sql import connect as connect_solidset_sql
from app.rag.vector_store import ensure_vector_collection


# Tablas de negocio físicas verificadas en ISIFrameIsicom.sql. Los archivos se
# excluyen porque tienen pipelines con permisos propios y contenido sensible.
DEFAULT_BUSINESS_TABLES = (
    "Activity", "SysTask", "SysWorkRoom", "SysResources", "SysLogin",
    "Entity", "SysCommunity", "SysPerson", "SysChat",
)

# Estas tablas describen relaciones y no deben producir cientos de miles de
# vectores aislados. Sus filas se incorporan al documento de la entidad padre.
RELATION_AGGREGATIONS: dict[str, tuple[dict[str, Any], ...]] = {
    "Activity": (
        {"table": "Activity2Channel", "parent_key": "IDActivity", "relation_key": "IDActivity",
         "fields": ("IDChannel", "Participation")},
        {"table": "SysActivityResourceRoleActivity", "parent_key": "IDActivity", "relation_key": "IDActivity",
         "fields": ("IDResource", "IDUser", "IDRole", "IDChannel", "ParticipationActive", "AssignMode", "Kind", "LinkState")},
    ),
    "SysTask": (
        {"table": "SysTask2Channel", "parent_key": "IDTask", "relation_key": "IDTask",
         "fields": ("IDChannel", "Participation")},
        {"table": "SysTaskResourceRole", "parent_key": "IDTask", "relation_key": "IDTask",
         "fields": ("IDResource", "IDUser", "IDActivityRole", "IDChannel", "AssignMode", "Kind", "LinkState")},
    ),
    "SysWorkRoom": (
        {"table": "SysWorkRoomResource", "parent_key": "IDWorkRoom", "relation_key": "IDWorkRoom",
         "fields": ("IDResource", "IDLogin", "IsOwner", "ResourceAccessType", "AllowPublish")},
        {"table": "SysCompany2Workroom", "parent_key": "IDWorkRoom", "relation_key": "IDWorkRoom",
         "fields": ("IDCompany",)},
        {"table": "SysCommunity2WorkRoom", "parent_key": "IDWorkRoom", "relation_key": "IDWorkRoom",
         "fields": ("IDCommunity", "IsDefault", "RelationType", "ChannelType")},
    ),
    "SysResources": (
        {"table": "SysLogin2SysResource", "parent_key": "ResourceId", "relation_key": "IDResource",
         "fields": ("IDLogin", "IDPerson", "IsDefault", "Active", "JoinRequestStatus")},
        {"table": "SysCommunity2Resource", "parent_key": "ResourceId", "relation_key": "IDResource",
         "fields": ("IDCommunity", "IsOwner", "IsSilenced", "JoinRequestStatus")},
    ),
    "Entity": (
        {"table": "SysCommunity2Company", "parent_key": "ID", "relation_key": "IDCompany",
         "fields": ("IDCommunity", "IsDefault", "JoinRequestStatus")},
        {"table": "SysCompany2Login", "parent_key": "ID", "relation_key": "IDCompany",
         "fields": ("IDLogin", "JoinRequestStatus")},
        {"table": "SysCompany2Workroom", "parent_key": "ID", "relation_key": "IDCompany",
         "fields": ("IDWorkRoom",)},
        # SysResources también conserva sus propios documentos. Aquí se usa
        # adicionalmente para autorizar el conocimiento de la empresa a todos
        # sus recursos, no solo al creador del registro Entity.
        {"table": "SysResources", "parent_key": "ID", "relation_key": "IDCompany",
         "fields": ("ResourceId", "DisplayName", "Active"), "keep_documents": True},
    ),
    "SysCommunity": (
        {"table": "SysCommunity2Resource", "parent_key": "ID", "relation_key": "IDCommunity",
         "fields": ("IDResource", "IsOwner", "IsSilenced", "JoinRequestStatus")},
        {"table": "SysCommunity2Company", "parent_key": "ID", "relation_key": "IDCommunity",
         "fields": ("IDCompany", "IsDefault", "JoinRequestStatus")},
        {"table": "SysCommunity2WorkRoom", "parent_key": "ID", "relation_key": "IDCommunity",
         "fields": ("IDWorkRoom", "IsDefault", "RelationType", "ChannelType")},
    ),
    "SysLogin": (
        {"table": "SysCompany2Login", "parent_key": "IDLogin", "relation_key": "IDLogin",
         "fields": ("IDCompany", "JoinRequestStatus")},
    ),
    "SysChat": (
        {"table": "SysChat2Activity", "parent_key": "IDChat2", "relation_key": "IDChat",
         "fields": ("IDActivity",)},
        {"table": "SysChat2Record", "parent_key": "IDChat2", "relation_key": "IDChat",
         "fields": ("IDRecord", "GIDRecord", "IDRecordModule", "RecordCode", "RecordShortName", "RelatedRecordKind")},
        {"table": "SysChat2SysResource", "parent_key": "IDChat2", "relation_key": "IDChat",
         "fields": ("IDResource", "IDLogin", "Type", "LinkKind", "IDChannel", "Sequence", "IDMeeting")},
        {"table": "SysChat2SysWorkRoom", "parent_key": "IDChat2", "relation_key": "IDChat2",
         "fields": ("IDWorkRoom", "Kind", "IDMeeting")},
        {"table": "SysChat2Task", "parent_key": "IDChat2", "relation_key": "IDChat",
         "fields": ("IDTask",)},
    ),
}

RELATION_TABLES = tuple(dict.fromkeys(
    str(spec["table"])
    for specs in RELATION_AGGREGATIONS.values()
    for spec in specs
    if not spec.get("keep_documents")
))

TABLE_PURPOSES = {
    "Activity": "actividades, planificación, estado, responsables y fechas",
    "SysTask": "tareas, asignaciones, estado, progreso, prioridad y fechas",
    "SysResources": "recursos del sistema, identidad operativa, estado y empresa",
    "SysLogin": "usuarios y nombres de acceso asociados a personas y recursos",
    "SysWorkRoom": "canales o salas de trabajo del sistema",
    "Entity": "empresas y organizaciones del sistema; Entity.ID es la identidad de empresa usada por las relaciones de comunidad, recursos, logins y canales",
    "SysCommunity": "comunidades, empresas vinculadas, recursos miembros y canales relacionados",
    "SysPerson": "personas del sistema vinculadas con recursos y logins",
    "SysChat": "mensajes y conversaciones relacionados con canales, recursos, actividades, tareas y registros",
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
        ALTER TABLE public."SysAgentIASystemIngestionRun"
          ADD COLUMN IF NOT EXISTS "RequestedTables" jsonb,
          ADD COLUMN IF NOT EXISTS "AttemptCount" integer NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS "NextRetryAt" timestamptz,
          ADD COLUMN IF NOT EXISTS "HeartbeatAt" timestamptz,
          ADD COLUMN IF NOT EXISTS "WorkerID" varchar(255),
          ADD COLUMN IF NOT EXISTS "CheckpointTable" varchar(255),
          ADD COLUMN IF NOT EXISTS "CheckpointKey" jsonb,
          ADD COLUMN IF NOT EXISTS "CompletedTables" jsonb NOT NULL DEFAULT '[]'::jsonb;
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
        "AttemptCount", "NextRetryAt", "HeartbeatAt", "WorkerID",
        "CheckpointTable", "CheckpointKey", "CompletedTables",
    }
    assignments, params = [], []
    for key, value in values.items():
        if key in allowed:
            assignments.append(f'"{key}"=%s')
            params.append(Jsonb(value) if key in {"CheckpointKey", "CompletedTables"} and value is not None else value)
    if not assignments:
        return
    params.append(uuid.UUID(run_id))
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE public."SysAgentIASystemIngestionRun" SET {", ".join(assignments)}, '
            '"UpdatedAt"=CURRENT_TIMESTAMP WHERE "ID"=%s', params,
        )


def create_run(
    instance: dict[str, Any], requested_tables: list[str] | None = None,
) -> str:
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
          ("ID","IDSolidSETInstance","InstanceCode","Status","RequestedTables")
          VALUES (%s,%s,%s,'queued',%s::jsonb)''',
          (run_id, instance_id, str(instance.get("Code") or ""),
           json.dumps(requested_tables) if requested_tables else None))
        return str(run_id)


def claim_next_run(worker_id: str, stale_seconds: int) -> dict[str, Any] | None:
    """Adquiere una ejecución pendiente o abandonada mediante un lease persistente."""
    ensure_system_knowledge_schema()
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''
          SELECT * FROM public."SysAgentIASystemIngestionRun"
          WHERE "Status"='queued'
             OR ("Status"='retrying' AND COALESCE("NextRetryAt",CURRENT_TIMESTAMP) <= CURRENT_TIMESTAMP)
             OR ("Status"='running' AND (
                   COALESCE("HeartbeatAt","UpdatedAt")
                     < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                   OR COALESCE("WorkerID",'') LIKE %s
                 ))
          ORDER BY CASE WHEN "Status"='running' THEN 0 ELSE 1 END, "StartedAt"
          FOR UPDATE SKIP LOCKED LIMIT 1
        ''', (max(60, int(stale_seconds)), f"{worker_id.split(':', 1)[0]}:%"))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute('''UPDATE public."SysAgentIASystemIngestionRun"
          SET "Status"='running',"WorkerID"=%s,"HeartbeatAt"=CURRENT_TIMESTAMP,
              "UpdatedAt"=CURRENT_TIMESTAMP,"Error"=NULL,"CompletedAt"=NULL,
              "AttemptCount"="AttemptCount"+1
          WHERE "ID"=%s RETURNING *''', (worker_id, row["ID"]))
        claimed = dict(cur.fetchone())
        for key, value in list(claimed.items()):
            if isinstance(value, (uuid.UUID, datetime)):
                claimed[key] = str(value)
        return claimed


def heartbeat_run(run_id: str, worker_id: str) -> None:
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIASystemIngestionRun"
          SET "HeartbeatAt"=CURRENT_TIMESTAMP,"UpdatedAt"=CURRENT_TIMESTAMP
          WHERE "ID"=%s AND "Status"='running' AND "WorkerID"=%s''',
          (uuid.UUID(run_id), worker_id))


def retry_run(run_id: str, error: str, delay_seconds: int) -> None:
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIASystemIngestionRun"
          SET "Status"='retrying',"Error"=%s,"WorkerID"=NULL,"HeartbeatAt"=NULL,
              "CompletedAt"=NULL,"NextRetryAt"=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
              "UpdatedAt"=CURRENT_TIMESTAMP WHERE "ID"=%s''',
          (str(error)[:4000], max(10, int(delay_seconds)), uuid.UUID(run_id)))


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
        heartbeat = result.get("HeartbeatAt") or result.get("UpdatedAt")
        heartbeat_dt = datetime.fromisoformat(heartbeat) if isinstance(heartbeat, str) else heartbeat
        seconds = max(0, int((datetime.now(timezone.utc) - heartbeat_dt).total_seconds())) if heartbeat_dt else None
        result["SecondsWithoutHeartbeat"] = seconds
        result["Alive"] = bool(
            result.get("Status") == "running" and seconds is not None
            and seconds <= settings.SYSTEM_KNOWLEDGE_STALE_SECONDS
        )
        result["ExecutionState"] = (
            "stale" if result.get("Status") == "running" and not result["Alive"]
            else result.get("Status")
        )
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


def _semantic_text(
    table: str, row: dict[str, Any], relation_context: str = "",
) -> str:
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
    text = f"Entidad SolidSET {table}. Contiene {purpose}. " + ". ".join(facts) + "."
    return f"{text} {relation_context}".strip()


def _relation_context_for_rows(
    connection: Any, parent_table: str, rows: list[dict[str, Any]],
    available_tables: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Agrupa relaciones por entidad padre sin crear documentos vectoriales propios."""
    result: dict[str, dict[str, Any]] = {}
    for spec in RELATION_AGGREGATIONS.get(parent_table, ()):
        relation_table = str(spec["table"])
        if available_tables is not None:
            actual = available_tables.get(relation_table.lower())
            if not actual:
                continue
            relation_table = actual
        parent_key = str(spec["parent_key"])
        relation_key = str(spec["relation_key"])
        values = list(dict.fromkeys(row.get(parent_key) for row in rows if row.get(parent_key) is not None))
        if not values:
            continue
        fields = tuple(str(value) for value in spec["fields"])
        select_fields = (relation_key,) + fields
        placeholders = ",".join("%s" for _ in values)
        with connection.cursor(as_dict=True) as cur:
            cur.execute(
                f"SELECT {', '.join(_quote(value) for value in select_fields)} "
                f"FROM dbo.{_quote(relation_table)} WITH (NOLOCK) "
                f"WHERE {_quote(relation_key)} IN ({placeholders})",
                tuple(values),
            )
            related_rows = [dict(value) for value in (cur.fetchall() or [])]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for related in related_rows:
            grouped.setdefault(str(related.get(relation_key)), []).append(related)
        for parent_value, relations in grouped.items():
            entry = result.setdefault(parent_value, {"parts": [], "resource_ids": []})
            rendered = []
            for relation in relations[:20]:
                facts = [
                    f"{field}={relation.get(field)}" for field in fields
                    if relation.get(field) is not None
                ]
                if facts:
                    rendered.append(", ".join(facts))
                entry["resource_ids"].extend(_related_resources(relation))
            suffix = f"; se muestran 20 de {len(relations)}" if len(relations) > 20 else ""
            entry["parts"].append(
                f"Relaciones {spec['table']} ({len(relations)}): "
                f"{' | '.join(rendered) or 'sin atributos'}{suffix}."
            )
    for entry in result.values():
        entry["text"] = " ".join(entry.pop("parts"))
        entry["resource_ids"] = list(dict.fromkeys(entry["resource_ids"]))
    return result


def _upsert_documents(documents: list[dict[str, Any]], run_id: str) -> int:
    if not documents:
        return 0
    embeddings = OllamaEmbeddings(
        base_url=settings.SYSTEM_KNOWLEDGE_EMBEDDING_BASE_URL,
        model=settings.EMBEDDING_MODEL_NAME,
    )
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
    incoming = [{
        "instance_id": item["instance_id"], "source_table": item["table"],
        "source_record_id": item["record_id"], "content_hash": item["content_hash"],
    } for item in documents]
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''WITH incoming AS (
            SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
              instance_id uuid, source_table text, source_record_id text, content_hash text)
          )
          SELECT i.source_table,i.source_record_id,d."ContentHash"
          FROM incoming i LEFT JOIN public."SysAgentIASystemDocument" d
            ON d."IDSolidSETInstance"=i.instance_id
           AND d."SourceTable"=i.source_table
           AND d."SourceRecordID"=i.source_record_id AND d."Status"='indexed' ''',
          (Jsonb(incoming),))
        existing = {
            (str(row["source_table"]), str(row["source_record_id"])): row["ContentHash"]
            for row in cur.fetchall() or []
        }
        unchanged_items = [item for item in documents if existing.get(
            (item["table"], item["record_id"])
        ) == item["content_hash"]]
        changed = [item for item in documents if existing.get(
            (item["table"], item["record_id"])
        ) != item["content_hash"]]
        if unchanged_items:
            unchanged_payload = [{
                "instance_id": item["instance_id"], "source_table": item["table"],
                "source_record_id": item["record_id"],
            } for item in unchanged_items]
            cur.execute('''WITH incoming AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                  instance_id uuid, source_table text, source_record_id text)
              )
              UPDATE public."SysAgentIASystemDocument" d
              SET "LastSeenRunID"=%s,"DeletedAt"=NULL,"Status"='indexed'
              FROM incoming i WHERE d."IDSolidSETInstance"=i.instance_id
                AND d."SourceTable"=i.source_table
                AND d."SourceRecordID"=i.source_record_id''',
              (Jsonb(unchanged_payload), uuid.UUID(run_id)))
    return changed, len(unchanged_items)


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


def _retire_relation_documents(instance_id: str) -> int:
    """Elimina vectores legados de tablas que ahora se agregan a la entidad padre."""
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIASystemDocument"
          SET "Status"='deleted',"DeletedAt"=CURRENT_TIMESTAMP
          WHERE "IDSolidSETInstance"=%s AND "SourceTable"=ANY(%s)
            AND "Status"='indexed' RETURNING "QdrantPointID"''',
          (uuid.UUID(instance_id), list(RELATION_TABLES)))
        points = [str(row["QdrantPointID"]) for row in cur.fetchall() or []]
    if points:
        qdrant = QdrantClient(url=settings.VECTOR_DB_URL)
        for offset in range(0, len(points), 1000):
            qdrant.delete(
                collection_name=settings.VECTOR_COLLECTION_NAME,
                points_selector=PointIdsList(points[offset:offset + 1000]), wait=True,
            )
    return len(points)


def _run_progress(run_id: str) -> dict[str, Any]:
    with _pg_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT "RowsRead","RowsIndexed","RowsSkipped",
          "CheckpointTable","CheckpointKey","CompletedTables"
          FROM public."SysAgentIASystemIngestionRun" WHERE "ID"=%s''',
          (uuid.UUID(run_id),))
        row = cur.fetchone()
        return dict(row) if row else {}


def _keyset_where(
    order_columns: list[str], checkpoint: dict[str, Any] | None,
) -> tuple[str, tuple[Any, ...]]:
    if not checkpoint:
        return "", ()
    predicates: list[str] = []
    parameters: list[Any] = []
    for index, column in enumerate(order_columns):
        prefix = []
        for previous in order_columns[:index]:
            prefix.append(f"{_quote(previous)}=%s")
            parameters.append(checkpoint[previous])
        prefix.append(f"{_quote(column)}>%s")
        parameters.append(checkpoint[column])
        predicates.append("(" + " AND ".join(prefix) + ")")
    return " WHERE " + " OR ".join(predicates), tuple(parameters)


def _checkpoint_from_row(row: dict[str, Any], order_columns: list[str]) -> dict[str, Any]:
    return {
        column: (
            value.isoformat() if isinstance((value := row.get(column)), datetime)
            else str(value) if isinstance(value, uuid.UUID)
            else value
        )
        for column in order_columns
    }


def run_system_knowledge_ingestion(
    run_id: str, instance: dict[str, Any], requested_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Ejecuta una fotografía incremental reanudable mediante keyset pagination."""
    read = indexed = skipped = completed = 0
    try:
        _run_update(run_id, Status="running", HeartbeatAt=datetime.now(timezone.utc))
        with connect_solidset_sql(instance, as_dict=True) as connection:
            requested = list(requested_tables or DEFAULT_BUSINESS_TABLES)
            relation_to_parent = {
                str(spec["table"]).lower(): parent
                for parent, specs in RELATION_AGGREGATIONS.items()
                for spec in specs
                if not spec.get("keep_documents")
            }
            entity_requested: list[str] = []
            for name in requested:
                parent = relation_to_parent.get(str(name).lower())
                candidate = parent or str(name)
                if candidate not in entity_requested:
                    entity_requested.append(candidate)
            relation_requested = [
                str(spec["table"])
                for parent in entity_requested
                for spec in RELATION_AGGREGATIONS.get(parent, ())
            ]
            catalog_requested = list(dict.fromkeys(entity_requested + relation_requested))
            catalog, primary_keys = _catalog(connection, catalog_requested)
            lookup = {name.lower(): name for name in catalog}
            missing = [name for name in requested if name.lower() not in lookup]
            if requested_tables and missing:
                raise ValueError(f"Tablas inexistentes o no accesibles: {', '.join(missing)}")
            tables = [lookup[name.lower()] for name in entity_requested if name.lower() in lookup]
            progress = _run_progress(run_id)
            completed_tables = [str(value) for value in (progress.get("CompletedTables") or [])]
            checkpoint_table = str(progress.get("CheckpointTable") or "")
            checkpoint_key = progress.get("CheckpointKey") or None
            if not checkpoint_table and not completed_tables:
                # Ejecuciones antiguas no tenían checkpoint: sus contadores no son reanudables.
                read = indexed = skipped = completed = 0
                _run_update(
                    run_id, RowsRead=0, RowsIndexed=0, RowsSkipped=0,
                    TablesCompleted=0, CompletedTables=[], CheckpointKey=None,
                )
            else:
                read = int(progress.get("RowsRead") or 0)
                indexed = int(progress.get("RowsIndexed") or 0)
                skipped = int(progress.get("RowsSkipped") or 0)
                completed = len(completed_tables)
            _run_update(run_id, TablesTotal=len(tables), TablesCompleted=completed)
            for table in tables:
                if table in completed_tables:
                    continue
                table_checkpoint = checkpoint_key if checkpoint_table == table else None
                _run_update(
                    run_id, CurrentTable=table, CheckpointTable=table,
                    CheckpointKey=table_checkpoint,
                )
                columns = _safe_columns(catalog[table])
                if not columns:
                    skipped += 1; completed += 1
                    completed_tables.append(table)
                    _run_update(
                        run_id, TablesCompleted=completed, RowsSkipped=skipped,
                        CompletedTables=completed_tables, CheckpointKey=None,
                    )
                    continue
                order_columns = primary_keys.get(table) or []
                if not order_columns:
                    raise ValueError(
                        f"La tabla {table} no tiene clave primaria; no puede reanudarse "
                        "de forma segura mediante paginación keyset."
                    )
                schema_text = (
                    f"Catálogo de la entidad SolidSET {table}. Finalidad: "
                    f"{TABLE_PURPOSES.get(table, f'datos de negocio de {table}')}. "
                    f"Columnas disponibles: {', '.join(columns)}. "
                    f"Clave primaria: {', '.join(order_columns)}. "
                    f"Relaciones agregadas: {', '.join(str(spec['table']) for spec in RELATION_AGGREGATIONS.get(table, ())) or 'ninguna'}."
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
                order_by = ", ".join(_quote(column) for column in order_columns)
                page_size = min(500, max(50, int(settings.SYSTEM_KNOWLEDGE_BATCH_SIZE)))
                while True:
                    where_sql, where_parameters = _keyset_where(order_columns, table_checkpoint)
                    with connection.cursor(as_dict=True) as cur:
                        cur.execute(
                            f"SELECT TOP {page_size} {select_list} "
                            f"FROM dbo.{_quote(table)} WITH (NOLOCK){where_sql} "
                            f"ORDER BY {order_by}",
                            where_parameters,
                        )
                        rows = cur.fetchall() or []
                    if not rows:
                        break
                    materialized_rows = [dict(raw) for raw in rows]
                    relation_context = _relation_context_for_rows(
                        connection, table, materialized_rows, lookup,
                    )
                    documents = []
                    for row in materialized_rows:
                        relation = relation_context.get(str(row.get(
                            next((str(spec["parent_key"]) for spec in RELATION_AGGREGATIONS.get(table, ())),
                                 order_columns[0])
                        )), {})
                        record_id = _record_id(row, order_columns)
                        text = _semantic_text(table, row, str(relation.get("text") or ""))
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
                                "source_record_id": record_id,
                                "related_resource_ids": list(dict.fromkeys(
                                    _related_resources(row) + list(relation.get("resource_ids") or [])
                                )),
                                "content_hash": digest, "indexed_at": datetime.now(timezone.utc).isoformat(),
                                "generated_by_ia": False,
                            },
                        })
                    changed, unchanged = _documents_requiring_embedding(documents, run_id)
                    skipped += unchanged
                    for offset in range(0, len(changed), 100):
                        waited = wait_for_interactive_idle()
                        if waited >= 0.5:
                            print(
                                f"⏸️ Ingesta de sistema cedió {waited:.1f}s al chat "
                                f"run={run_id}", flush=True,
                            )
                        indexed += _upsert_documents(changed[offset:offset + 100], run_id)
                        _run_update(
                            run_id, RowsRead=read, RowsIndexed=indexed,
                            RowsSkipped=skipped,
                        )
                    read += len(rows)
                    table_checkpoint = _checkpoint_from_row(materialized_rows[-1], order_columns)
                    _run_update(
                        run_id, RowsRead=read, RowsIndexed=indexed, RowsSkipped=skipped,
                        CheckpointTable=table, CheckpointKey=table_checkpoint,
                    )
                    if len(rows) < page_size:
                        break
                skipped += _retire_missing_documents(str(instance["ID"]), table, run_id)
                completed += 1
                completed_tables.append(table)
                checkpoint_table, checkpoint_key = table, None
                _run_update(
                    run_id, TablesCompleted=completed, RowsRead=read,
                    RowsIndexed=indexed, RowsSkipped=skipped,
                    CompletedTables=completed_tables, CheckpointTable=table,
                    CheckpointKey=None,
                )
            skipped += _retire_relation_documents(str(instance["ID"]))
        _run_update(
            run_id, Status="completed", CurrentTable=None, TablesCompleted=completed,
            RowsRead=read, RowsIndexed=indexed, RowsSkipped=skipped,
            CompletedAt=datetime.now(timezone.utc), WorkerID=None,
            HeartbeatAt=datetime.now(timezone.utc), NextRetryAt=None,
            CheckpointTable=None, CheckpointKey=None,
        )
        return get_run_status(run_id=run_id) or {}
    except Exception as exc:
        _run_update(
            run_id, Status="failed", Error=str(exc)[:4000], RowsRead=read,
            RowsIndexed=indexed, RowsSkipped=skipped, TablesCompleted=completed,
            CompletedAt=datetime.now(timezone.utc),
        )
        raise
