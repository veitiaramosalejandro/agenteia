from __future__ import annotations

from typing import Any

import pymssql
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import connection, execute_read
from app.security import valid_api_key, validate_read_query
from app.queries import ACTIVE_RESOURCE_AGENT, DATASETS


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
                "resourceAgents": bool(resource_agent.get("HasResourceAgent")),
            },
        }
    except pymssql.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível ligar ao SQL Server.") from exc


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
        columns, rows = execute_read(query, request.parameters, limit)
        return QueryResponse(columns=columns, rows=rows, rowCount=len(rows))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except pymssql.Error as exc:
        raise HTTPException(status_code=503, detail="A consulta SQL Server falhou.") from exc


@app.get(
    "/api/v1/datasets/{dataset}",
    response_model=QueryResponse,
    tags=["SolidSET datasets"],
    dependencies=[Depends(require_api_key)],
)
def read_dataset(dataset: str) -> QueryResponse:
    query = DATASETS.get(dataset.strip().lower())
    if not query:
        raise HTTPException(status_code=404, detail="O conjunto de dados não existe.")
    try:
        columns, rows = execute_read(
            query, [], settings.SOLIDSET_DATA_API_MAX_ROWS
        )
        return QueryResponse(columns=columns, rows=rows, rowCount=len(rows))
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
        columns, rows = execute_read(ACTIVE_RESOURCE_AGENT, [human_resource_id], 1)
        return QueryResponse(columns=columns, rows=rows, rowCount=len(rows))
    except pymssql.Error as exc:
        raise HTTPException(status_code=503, detail="A validação do agente falhou.") from exc
