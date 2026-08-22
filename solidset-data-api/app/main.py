from __future__ import annotations

from typing import Any
import re

import pymssql
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.database import connection, execute_read
from app.security import valid_api_key, validate_read_query
from app.queries import ACTIVE_RESOURCE_AGENT, DATASETS, DATASET_ORDER_BY


app = FastAPI(
    title="SolidSET Data API",
    version="1.0.0",
    description="Read-only SQL Server gateway for SolidSET AI integrations.",
)


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=6, max_length=100_000)
    parameters: list[Any] = Field(default_factory=list, max_length=2_000)
    maxRows: int | None = Field(None, ge=1)


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    rowCount: int
    offset: int | None = None
    limit: int | None = None
    hasMore: bool = False
    nextOffset: int | None = None


class SchemaColumn(BaseModel):
    name: str
    dataType: str
    nullable: bool
    maxLength: int | None = None
    primaryKey: bool = False


class SchemaForeignKey(BaseModel):
    name: str
    column: str
    referencedSchema: str
    referencedTable: str
    referencedColumn: str


class SchemaTable(BaseModel):
    schemaName: str
    tableName: str
    columns: list[SchemaColumn]
    foreignKeys: list[SchemaForeignKey]


class SchemaCatalogResponse(BaseModel):
    databaseName: str
    tables: list[SchemaTable]


def _sql_error_detail(exc: pymssql.Error) -> tuple[int, dict[str, Any]]:
    """Return a bounded, structured error that allows one safe schema correction."""
    message = str(exc)
    identifier_match = re.search(r"Invalid (?:column|object) name '([^']+)'", message)
    identifier = identifier_match.group(1) if identifier_match else None
    if "Invalid column name" in message:
        return 422, {
            "code": "COLUMN_NOT_FOUND",
            "identifier": identifier,
            "message": "A consulta usa uma coluna inexistente. Atualize o catálogo e corrija a consulta.",
        }
    if "Invalid object name" in message:
        return 422, {
            "code": "TABLE_NOT_FOUND",
            "identifier": identifier,
            "message": "A consulta usa uma tabela inexistente. Atualize o catálogo e corrija a consulta.",
        }
    return 503, {
        "code": "SQL_READ_FAILED",
        "identifier": None,
        "message": "A consulta de leitura ao SQL Server falhou.",
    }


def require_api_key(x_solidset_data_key: str | None = Header(None)) -> None:
    if not valid_api_key(x_solidset_data_key, settings.SOLIDSET_DATA_API_KEY):
        raise HTTPException(status_code=401, detail="Credencial da SolidSET Data API inválida.")


@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/system/capabilities", tags=["System"], dependencies=[Depends(require_api_key)])
def capabilities() -> dict[str, Any]:
    try:
        with connection(as_dict=True) as conn, conn.cursor(as_dict=True) as cursor:
            cursor.execute("SELECT DB_NAME() AS DatabaseName, @@VERSION AS ServerVersion")
            server = cursor.fetchone() or {}
            cursor.execute(
                "SELECT CASE WHEN OBJECT_ID('dbo.SysResource2Agent') IS NULL "
                "THEN 0 ELSE 1 END AS HasResourceAgent"
            )
            resource_agent = cursor.fetchone() or {}
        return {
            "apiVersion": "1.0",
            "adapterCode": "solidset-v1",
            "databaseName": server.get("DatabaseName"),
            "serverVersion": str(server.get("ServerVersion") or "")[:255],
            "capabilities": {
                "readQuery": True,
                "schemaCatalog": True,
                "resourceAgents": bool(resource_agent.get("HasResourceAgent")),
            },
        }
    except pymssql.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível ligar ao SQL Server.") from exc


@app.get(
    "/api/v1/schema/catalog",
    response_model=SchemaCatalogResponse,
    tags=["Database schema"],
    dependencies=[Depends(require_api_key)],
    summary="Read the SQL Server schema catalog",
    description=(
        "Returns tables, columns, primary keys and foreign keys. Use the optional "
        "tables parameter to retrieve only the fragment needed for a dynamic query."
    ),
)
def schema_catalog(tables: str | None = Query(None, max_length=4000)) -> SchemaCatalogResponse:
    requested = {
        value.strip().lower()
        for value in str(tables or "").split(",")
        if value.strip()
    }
    try:
        with connection(as_dict=True) as conn, conn.cursor(as_dict=True) as cursor:
            cursor.execute("SELECT DB_NAME() AS DatabaseName")
            database_name = str((cursor.fetchone() or {}).get("DatabaseName") or "")
            cursor.execute(
                """
                SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
                       c.IS_NULLABLE, c.CHARACTER_MAXIMUM_LENGTH,
                       CASE WHEN pk.COLUMN_NAME IS NULL THEN 0 ELSE 1 END AS IsPrimaryKey
                FROM INFORMATION_SCHEMA.COLUMNS c
                LEFT JOIN (
                    SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
                    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                    INNER JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                      ON ku.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                     AND ku.TABLE_SCHEMA = tc.TABLE_SCHEMA
                    WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                ) pk ON pk.TABLE_SCHEMA = c.TABLE_SCHEMA
                    AND pk.TABLE_NAME = c.TABLE_NAME
                    AND pk.COLUMN_NAME = c.COLUMN_NAME
                WHERE c.TABLE_SCHEMA = 'dbo'
                ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
                """
            )
            column_rows = cursor.fetchall() or []
            cursor.execute(
                """
                SELECT fk.name AS ForeignKeyName,
                       OBJECT_SCHEMA_NAME(fkc.parent_object_id) AS TableSchema,
                       OBJECT_NAME(fkc.parent_object_id) AS TableName,
                       pc.name AS ColumnName,
                       OBJECT_SCHEMA_NAME(fkc.referenced_object_id) AS ReferencedSchema,
                       OBJECT_NAME(fkc.referenced_object_id) AS ReferencedTable,
                       rc.name AS ReferencedColumn
                FROM sys.foreign_key_columns fkc
                INNER JOIN sys.foreign_keys fk ON fk.object_id = fkc.constraint_object_id
                INNER JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id
                    AND pc.column_id = fkc.parent_column_id
                INNER JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id
                    AND rc.column_id = fkc.referenced_column_id
                ORDER BY TableSchema, TableName, fk.name, fkc.constraint_column_id
                """
            )
            fk_rows = cursor.fetchall() or []
    except pymssql.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível ler o esquema SQL Server.") from exc

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in column_rows:
        schema_name = str(row.get("TABLE_SCHEMA") or "")
        table_name = str(row.get("TABLE_NAME") or "")
        qualified = f"{schema_name}.{table_name}".lower()
        if requested and table_name.lower() not in requested and qualified not in requested:
            continue
        table = grouped.setdefault((schema_name, table_name), {
            "schemaName": schema_name,
            "tableName": table_name,
            "columns": [],
            "foreignKeys": [],
        })
        table["columns"].append({
            "name": str(row.get("COLUMN_NAME") or ""),
            "dataType": str(row.get("DATA_TYPE") or ""),
            "nullable": str(row.get("IS_NULLABLE") or "").upper() == "YES",
            "maxLength": row.get("CHARACTER_MAXIMUM_LENGTH"),
            "primaryKey": bool(row.get("IsPrimaryKey")),
        })
    for row in fk_rows:
        key = (str(row.get("TableSchema") or ""), str(row.get("TableName") or ""))
        if key not in grouped:
            continue
        grouped[key]["foreignKeys"].append({
            "name": str(row.get("ForeignKeyName") or ""),
            "column": str(row.get("ColumnName") or ""),
            "referencedSchema": str(row.get("ReferencedSchema") or ""),
            "referencedTable": str(row.get("ReferencedTable") or ""),
            "referencedColumn": str(row.get("ReferencedColumn") or ""),
        })
    return SchemaCatalogResponse(databaseName=database_name, tables=list(grouped.values()))


@app.post(
    "/api/v1/query/read",
    response_model=QueryResponse,
    tags=["Read-only queries"],
    dependencies=[Depends(require_api_key)],
)
def read_query(request: QueryRequest) -> QueryResponse:
    try:
        query = validate_read_query(request.query)
        limit = min(request.maxRows or settings.SOLIDSET_DATA_API_MAX_ROWS,
                    settings.SOLIDSET_DATA_API_MAX_ROWS)
        columns, rows = execute_read(
            query,
            request.parameters,
            limit,
            operation="ad-hoc-read",
        )
        return QueryResponse(columns=columns, rows=rows, rowCount=len(rows))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except pymssql.Error as exc:
        status_code, detail = _sql_error_detail(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.get(
    "/api/v1/datasets/{dataset}",
    response_model=QueryResponse,
    tags=["SolidSET datasets"],
    dependencies=[Depends(require_api_key)],
)
def read_dataset(
    dataset: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1),
) -> QueryResponse:
    dataset_code = dataset.strip().lower()
    query = DATASETS.get(dataset_code)
    if not query:
        raise HTTPException(status_code=404, detail="O conjunto de dados não existe.")
    try:
        page_size = min(limit, settings.SOLIDSET_DATA_API_MAX_ROWS)
        order_by = DATASET_ORDER_BY[dataset_code]
        paged_query = (
            f"SELECT * FROM ({query.strip().rstrip(';')}) AS DatasetPage "
            f"ORDER BY {order_by} OFFSET %s ROWS FETCH NEXT %s ROWS ONLY"
        )
        columns, rows = execute_read(
            paged_query,
            [offset, page_size + 1],
            page_size + 1,
            operation=f"dataset:{dataset_code}",
        )
        has_more = len(rows) > page_size
        page = rows[:page_size]
        return QueryResponse(
            columns=columns,
            rows=page,
            rowCount=len(page),
            offset=offset,
            limit=page_size,
            hasMore=has_more,
            nextOffset=(offset + len(page)) if has_more else None,
        )
    except pymssql.Error as exc:
        raise HTTPException(status_code=503, detail="A leitura do conjunto de dados falhou.") from exc


@app.get(
    "/api/v1/agents/{human_resource_id}",
    response_model=QueryResponse,
    tags=["SolidSET agents"],
    dependencies=[Depends(require_api_key)],
)
def read_active_resource_agent(human_resource_id: str) -> QueryResponse:
    try:
        columns, rows = execute_read(
            ACTIVE_RESOURCE_AGENT,
            [human_resource_id],
            1,
            operation="resource-agent-validation",
        )
        return QueryResponse(columns=columns, rows=rows, rowCount=len(rows))
    except pymssql.Error as exc:
        raise HTTPException(status_code=503, detail="A validação do agente falhou.") from exc
