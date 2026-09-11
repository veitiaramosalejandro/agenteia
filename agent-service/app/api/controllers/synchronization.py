from collections.abc import Callable

import psycopg
import pymssql
from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas.synchronization import (
    SolidSETCatalogPage,
    SysAgentIAModelSyncResponse,
    SysAgentIAScopeIngestResponse,
    SysChatIAResourceIngestResponse,
    SysLoginIngestResponse,
    SysResourceIAIngestResponse,
    SysWorkRoomIngestResponse,
)
from app.connectors.db_client import get_solidset_instance, synchronize_agent_model_defaults
from app.connectors.solidset_data_api import read_catalog_page, SolidSETDataAPIError
from app.system.resource_ingest import (
    ingest_solidset_agent_scopes,
    ingest_solidset_chat_resources,
    ingest_solidset_logins,
    ingest_solidset_resources,
    ingest_solidset_workrooms,
)


router = APIRouter(prefix="/api/v1/agent/solidset", tags=["SolidSET Configuration"])


@router.post("/agent-models/sync", response_model=SysAgentIAModelSyncResponse,
             summary="Complete default SysAgentIAModel assignments",
             description="Completa asignaciones faltantes de recursos activos ya sincronizados. "
                         "Conserva configuraciones personalizadas y no consulta SolidSET ni invoca un LLM.")
def sync_agent_models(
    instanceCode: str = Query(..., min_length=1, max_length=100, pattern=r"\S"),
) -> SysAgentIAModelSyncResponse:
    try:
        instance = get_solidset_instance(code=instanceCode.strip(), source_ip=None)
        if not instance:
            raise HTTPException(status_code=404, detail="A instância SolidSET não existe.")
        result = synchronize_agent_model_defaults(instance["ID"])
        return SysAgentIAModelSyncResponse(
            status="partial" if result["skippedNoProvider"] else "synchronized",
            synchronized=result["sourceRows"] - result["skippedNoProvider"],
            skipped=result["skippedNoProvider"],
            instanceCode=instance["Code"], **result,
        )
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Não foi possível sincronizar os modelos dos agentes.") from exc


def _catalog_page(instance_code: str, dataset: str, offset: int, limit: int) -> SolidSETCatalogPage:
    try:
        instance = get_solidset_instance(code=instance_code.strip(), source_ip=None)
        if not instance:
            raise HTTPException(status_code=404, detail="A instância SolidSET não existe.")
        configuration = instance.get("DataAPI") or {}
        if not configuration.get("active") or not configuration.get("BaseUrl"):
            raise HTTPException(status_code=503, detail="A SolidSET Data API não está ativa.")
        page = read_catalog_page(configuration, dataset, offset=offset, limit=limit)
        return SolidSETCatalogPage(instanceCode=instance["Code"], **page)
    except (psycopg.Error, SolidSETDataAPIError) as exc:
        raise HTTPException(status_code=503, detail="Não foi possível consultar o catálogo SolidSET.") from exc


@router.get("/workrooms", response_model=SolidSETCatalogPage, summary="List SolidSET workrooms",
            description="Reads one page from the selected instance Data API without synchronizing data.")
def list_solidset_workrooms(
    instanceCode: str = Query(..., min_length=1, max_length=100, pattern=r"\S"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> SolidSETCatalogPage:
    return _catalog_page(instanceCode, "workrooms", offset, limit)


@router.get("/resources", response_model=SolidSETCatalogPage, summary="List SolidSET resources",
            description="Reads one page from the selected instance Data API without synchronizing data.")
def list_solidset_resources(
    instanceCode: str = Query(..., min_length=1, max_length=100, pattern=r"\S"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> SolidSETCatalogPage:
    return _catalog_page(instanceCode, "resources", offset, limit)


def _synchronize(instance_code: str, operation: Callable, entity: str) -> dict:
    try:
        instance = get_solidset_instance(code=instance_code, source_ip=None)
        if not instance or not instance.get("DataAPI"):
            raise HTTPException(
                status_code=404, detail="A instância ou a SolidSET Data API não existe."
            )
        return operation(instance)
    except HTTPException:
        raise
    except (SolidSETDataAPIError, pymssql.Error, psycopg.Error, RuntimeError) as exc:
        print(f"❌ No se pudo sincronizar {entity}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Não foi possível sincronizar {entity}.",
        ) from exc


@router.post("/workrooms/sync", response_model=SysWorkRoomIngestResponse)
def sync_solidset_workrooms(
    instanceCode: str = Query(...),
) -> SysWorkRoomIngestResponse:
    return SysWorkRoomIngestResponse(
        status="synchronized",
        **_synchronize(instanceCode, ingest_solidset_workrooms, "os canais"),
    )

@router.post("/logins/sync", response_model=SysLoginIngestResponse)
def sync_solidset_logins(instanceCode: str = Query(...)) -> SysLoginIngestResponse:
    return SysLoginIngestResponse(
        status="synchronized",
        **_synchronize(instanceCode, ingest_solidset_logins, "as contas"),
    )


@router.post("/chat-workroom/sync", response_model=SysChatIAResourceIngestResponse)
def sync_solidset_chat_resources(
    instanceCode: str = Query(...),
) -> SysChatIAResourceIngestResponse:
    return SysChatIAResourceIngestResponse(
        status="synchronized",
        **_synchronize(
            instanceCode, ingest_solidset_chat_resources, "as relações de chat"
        ),
    )


@router.post("/agent-scopes/sync", response_model=SysAgentIAScopeIngestResponse)
def sync_solidset_agent_scopes(
    instanceCode: str = Query(...),
) -> SysAgentIAScopeIngestResponse:
    return SysAgentIAScopeIngestResponse(
        status="synchronized",
        **_synchronize(
            instanceCode, ingest_solidset_agent_scopes, "o alcance dos agentes"
        ),
    )


@router.post("/resources/sync", response_model=SysResourceIAIngestResponse)
def sync_solidset_resources(
    instanceCode: str = Query(...),
) -> SysResourceIAIngestResponse:
    return SysResourceIAIngestResponse(
        status="synchronized",
        **_synchronize(instanceCode, ingest_solidset_resources, "os recursos"),
    )
