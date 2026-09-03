from collections.abc import Callable

import psycopg
import pymssql
from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas.synchronization import (
    SysAgentIAScopeIngestResponse,
    SysChatIAResourceIngestResponse,
    SysLoginIngestResponse,
    SysResourceIAIngestResponse,
    SysWorkRoomIngestResponse,
)
from app.connectors.db_client import get_solidset_instance
from app.system.resource_ingest import (
    ingest_solidset_agent_scopes,
    ingest_solidset_chat_resources,
    ingest_solidset_logins,
    ingest_solidset_resources,
    ingest_solidset_workrooms,
)


router = APIRouter(prefix="/api/v1/agent/solidset", tags=["SolidSET synchronization"])


def _synchronize(instance_code: str, operation: Callable, entity: str) -> dict:
    try:
        instance = get_solidset_instance(code=instance_code, source_ip=None)
        if not instance or not instance.get("DataAPI"):
            raise HTTPException(status_code=404, detail="A instância ou a SolidSET Data API não existe.")
        return operation(instance)
    except HTTPException:
        raise
    except (pymssql.Error, psycopg.Error, RuntimeError) as exc:
        print(f"❌ No se pudo sincronizar {entity}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Não foi possível sincronizar {entity}.",
        ) from exc


@router.post("/workrooms/sync", response_model=SysWorkRoomIngestResponse)
def sync_solidset_workrooms(instanceCode: str = Query(...)) -> SysWorkRoomIngestResponse:
    return SysWorkRoomIngestResponse(
        status="synchronized", **_synchronize(instanceCode, ingest_solidset_workrooms, "os canais")
    )


@router.post("/logins/sync", response_model=SysLoginIngestResponse)
def sync_solidset_logins(instanceCode: str = Query(...)) -> SysLoginIngestResponse:
    return SysLoginIngestResponse(
        status="synchronized", **_synchronize(instanceCode, ingest_solidset_logins, "as contas")
    )


@router.post("/chat-workroom/sync", response_model=SysChatIAResourceIngestResponse)
def sync_solidset_chat_resources(instanceCode: str = Query(...)) -> SysChatIAResourceIngestResponse:
    return SysChatIAResourceIngestResponse(
        status="synchronized",
        **_synchronize(instanceCode, ingest_solidset_chat_resources, "as relações de chat"),
    )


@router.post("/agent-scopes/sync", response_model=SysAgentIAScopeIngestResponse)
def sync_solidset_agent_scopes(instanceCode: str = Query(...)) -> SysAgentIAScopeIngestResponse:
    return SysAgentIAScopeIngestResponse(
        status="synchronized",
        **_synchronize(instanceCode, ingest_solidset_agent_scopes, "o alcance dos agentes"),
    )


@router.post("/resources/sync", response_model=SysResourceIAIngestResponse)
def sync_solidset_resources(instanceCode: str = Query(...)) -> SysResourceIAIngestResponse:
    return SysResourceIAIngestResponse(
        status="synchronized",
        **_synchronize(instanceCode, ingest_solidset_resources, "os recursos"),
    )
