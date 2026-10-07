import asyncio
from typing import Any, Optional
from uuid import UUID

import psycopg
import pymssql
import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList

from app.api.schemas.ingestion import (
    HistoricalIngestionStartRequest,
    SystemKnowledgeIngestionStartRequest,
)
from app.config import settings
from app.connectors.db_client import (
    get_solidset_instance,
    list_active_solidset_instances,
)
from app.historical.producer import enqueue_next_batch
from app.historical.queue import HistoricalQueue
from app.historical.store import (
    approve_dry_run_cursors,
    historical_points,
    list_audits,
    list_cursors,
    mark_historical_deleted,
)
from app.system.system_knowledge_ingest import (
    create_run,
    get_run_status,
)


router = APIRouter(tags=["Historical Ingestion"])
historical_queue = HistoricalQueue()


def require_historical_admin(
    x_agent_admin_key: str = Header(..., alias="X-Agent-Admin-Key"),
) -> None:
    configured = settings.HISTORICAL_INGESTION_ADMIN_KEY.strip()
    if not configured:
        raise HTTPException(
            status_code=503, detail="Configure HISTORICAL_INGESTION_ADMIN_KEY."
        )
    if x_agent_admin_key != configured:
        raise HTTPException(
            status_code=401, detail="Credencial administrativa inválida."
        )


admin = [Depends(require_historical_admin)]


@router.post(
    "/api/v1/agent/system-knowledge-ingestion/start",
    status_code=202,
    dependencies=admin,
)
async def start_system_knowledge_ingestion(
    configuration: SystemKnowledgeIngestionStartRequest,
) -> dict[str, Any]:
    instance = get_solidset_instance(code=configuration.instanceCode, source_ip=None)
    if not instance:
        raise HTTPException(
            status_code=404, detail="Instância SolidSET não encontrada."
        )
    if not instance.get("DataAPI"):
        raise HTTPException(
            status_code=409, detail="A instância não possui Data API ativa."
        )
    try:
        run_id = await asyncio.to_thread(create_run, instance, configuration.tables)
    except (psycopg.Error, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível criar a execução."
        ) from exc
    return {
        "status": "queued",
        "runId": run_id,
        "statusUrl": f"/api/v1/agent/system-knowledge-ingestion/status?runId={run_id}",
    }


@router.get("/api/v1/agent/system-knowledge-ingestion/status", dependencies=admin)
def system_knowledge_ingestion_status(
    runId: Optional[UUID] = Query(None),
    instanceCode: Optional[str] = Query(None, min_length=1, max_length=100),
) -> dict[str, Any]:
    if not runId and not instanceCode:
        raise HTTPException(status_code=422, detail="Informe runId ou instanceCode.")
    instance_id = None
    if instanceCode:
        instance = get_solidset_instance(code=instanceCode, source_ip=None)
        if not instance:
            raise HTTPException(
                status_code=404, detail="Instância SolidSET não encontrada."
            )
        instance_id = str(instance["ID"])
    try:
        result = get_run_status(
            run_id=str(runId) if runId else None, instance_id=instance_id
        )
    except (psycopg.Error, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Estado da ingestão indisponível."
        ) from exc
    if not result:
        raise HTTPException(
            status_code=404, detail="Execução de ingestão não encontrada."
        )
    return result


@router.post(
    "/api/v1/agent/historical-ingestion/start", status_code=202, dependencies=admin
)
async def start_historical_ingestion(
    configuration: HistoricalIngestionStartRequest,
) -> dict[str, Any]:
    try:
        instances = (
            [get_solidset_instance(code=configuration.instanceCode, source_ip=None)]
            if configuration.instanceCode
            else list_active_solidset_instances()
        )
        instances = [item for item in instances if item]
        if not instances:
            raise HTTPException(
                status_code=404, detail="Não existem instâncias SolidSET ativas."
            )
        historical_queue.set_paused(False)
        results = [
            await asyncio.to_thread(enqueue_next_batch, item, configuration.dryRun)
            for item in instances
        ]
        return {
            "status": "accepted",
            "dryRun": configuration.dryRun,
            "instances": results,
        }
    except (pymssql.Error, psycopg.Error, redis.RedisError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível iniciar o lote de ingestão histórica.",
        ) from exc


@router.post("/api/v1/agent/historical-ingestion/pause", dependencies=admin)
def pause_historical_ingestion() -> dict[str, Any]:
    historical_queue.set_paused(True)
    return {"status": "paused"}


@router.post("/api/v1/agent/historical-ingestion/resume", dependencies=admin)
def resume_historical_ingestion() -> dict[str, Any]:
    historical_queue.set_paused(False)
    return {"status": "running"}


@router.post("/api/v1/agent/historical-ingestion/approve-dry-run", dependencies=admin)
def approve_historical_dry_run(instanceCode: str = Query(...)) -> dict[str, Any]:
    instance = get_solidset_instance(code=instanceCode, source_ip=None)
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada.")
    return {
        "status": "approved",
        "approvedCursors": approve_dry_run_cursors(str(instance["ID"])),
    }


@router.get("/api/v1/agent/historical-ingestion/status", dependencies=admin)
def historical_ingestion_status(
    resourceId: Optional[UUID] = Query(None),
) -> dict[str, Any]:
    return {
        "queue": historical_queue.stats(),
        "cursors": list_cursors(str(resourceId) if resourceId else None),
    }


@router.get("/api/v1/agent/historical-ingestion/batches", dependencies=admin)
def historical_ingestion_batches(
    limit: int = Query(50, ge=1, le=500),
    resourceId: Optional[UUID] = Query(None),
) -> dict[str, Any]:
    return {"items": list_audits(limit, str(resourceId) if resourceId else None)}


@router.delete(
    "/api/v1/agent/historical-ingestion/messages/{id_chat2}", dependencies=admin
)
def delete_historical_message(
    id_chat2: int,
    instanceCode: str = Query(...),
    sourceType: str = Query("chat", pattern="^(chat|task|activity)$"),
) -> dict[str, Any]:
    instance = get_solidset_instance(code=instanceCode, source_ip=None)
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada.")
    points = historical_points(str(instance["ID"]), id_chat2, sourceType)
    if points:
        QdrantClient(url=settings.VECTOR_DB_URL).delete(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            points_selector=PointIdsList(points=points),
            wait=True,
        )
    deleted = mark_historical_deleted(str(instance["ID"]), id_chat2, sourceType)
    return {
        "status": "deleted",
        "idChat2": id_chat2,
        "sourceType": sourceType,
        "documents": deleted,
    }
