from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response
from pydantic import BaseModel
import psycopg

from app.api.schemas.task_junta import JuntaTasksStartRequest, TaskJuntaData
from app.services.task_junta_runtime import cancel_operation, create_operation, get_operation
from app.services.task_similarity import analyze_task_similarity
from app.services.instance_resolution import _resolve_request_solidset_instance
from app.connectors.db_client import list_active_solidset_instances

router = APIRouter(tags=["Tasks"])


class JuntaSwaggerInstance(BaseModel):
    """Valores seguros para probar X-SolidSET-Instance desde Swagger."""

    code: str
    sourceIp: str | None = None
    baseUrl: str
    notificationConfigured: bool


@router.get(
    "/api/v1/agent/tasks/junta/test-instances",
    response_model=list[JuntaSwaggerInstance],
    summary="Listar instancias SolidSET para probar Junta",
    description=(
        "Devuelve las instancias activas registradas en PostgreSQL. "
        "Use el valor sourceIp como header X-SolidSET-Instance en el POST de Junta. "
        "No devuelve secretos ni la URL interna de NotificationManager."
    ),
)
def list_junta_test_instances() -> list[JuntaSwaggerInstance]:
    try:
        instances = list_active_solidset_instances()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudieron consultar las instancias SolidSET.") from exc

    return [
        JuntaSwaggerInstance(
            code=str(item.get("Code") or ""),
            sourceIp=str(item.get("SourceIP") or "") or None,
            baseUrl=str(item.get("BaseUrl") or ""),
            notificationConfigured=bool(str(item.get("NotificationUrl") or "").strip()),
        )
        for item in instances
        if str(item.get("Code") or "").strip()
    ]


def _status(operation) -> dict[str, object]:
    return {
        "status": operation.status,
        "operationId": str(operation.operation_id),
        "taskCount": len(operation.tasks),
        "processedPairs": operation.processed_pairs,
        "totalPairs": operation.total_pairs,
        "percent": round((operation.processed_pairs / operation.total_pairs) * 100, 2)
        if operation.total_pairs else (100 if operation.status == "completed" else 0),
        "error": operation.error,
    }


@router.post("/api/v1/agent/tasks/junta")
async def receive_junta_tasks(
    http_request: Request,
    response: Response,
    request: list[TaskJuntaData] | JuntaTasksStartRequest = Body(...),
    prefer: str | None = Header(default=None),
    solidset_instance: str | None = Header(
        default=None,
        alias="X-SolidSET-Instance",
        description="Host/IP de la instancia SolidSET de origen. Consulte GET /api/v1/agent/tasks/junta/test-instances.",
    ),
) -> dict[str, object]:
    """Acepta el contrato legado síncrono o inicia una operación Junta asíncrona."""
    if isinstance(request, JuntaTasksStartRequest):
        source_instance = _resolve_request_solidset_instance(http_request)
        if source_instance is None:
            raise HTTPException(
                status_code=400,
                detail="No se pudo resolver la instancia SolidSET de origen. Envíe X-SolidSET-Instance con un SourceIP configurado.",
            )

        notification_base = str(source_instance.get("NotificationUrl") or "").rstrip("/")
        if not notification_base:
            raise HTTPException(
                status_code=503,
                detail="La instancia SolidSET de origen no tiene NotificationUrl configurado.",
            )

        notification_url = f"{notification_base}/api/v1/internal/agent/tasks/junta/events"
        operation = await create_operation(
            operation_id=request.operationId,
            instance=request.instance,
            work_room_id=request.workRoomId,
            id_login=request.requestedBy.idLogin,
            id_resource=request.requestedBy.idResource,
            id_session=request.requestedBy.idSession,
            notification_url=notification_url,
            tasks=request.tasks,
            task_set_version=request.taskSetVersion,
            payload_hash=request.payloadHash,
        )
        response.status_code = 202
        return {
            "status": "accepted",
            "operationId": str(operation.operation_id),
            "count": len(operation.tasks),
            "statusUrl": f"/api/v1/agent/tasks/junta/{operation.operation_id}",
            "resultUrl": f"/api/v1/agent/tasks/junta/{operation.operation_id}/result",
        }

    if prefer and "respond-async" in prefer.lower():
        raise HTTPException(status_code=422, detail="El modo asíncrono requiere el contrato Junta v1.")
    try:
        result = analyze_task_similarity(request)
    except ValueError as exc:
        if str(exc) == "invalid_task_similarity_output":
            raise HTTPException(status_code=502, detail="El modelo devolvió una respuesta JSON incompleta o inválida.") from None
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        print(f"TASK_JUNTA_ANALYSIS_FAILED type={type(exc).__name__}", flush=True)
        raise HTTPException(status_code=503, detail="No se pudo analizar la similitud de las tareas.") from None
    return {"status": "analyzed", "count": len(request), **result}


@router.get("/api/v1/agent/tasks/junta/{operation_id}")
async def get_junta_operation(operation_id: UUID) -> dict[str, object]:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    return _status(operation)


@router.get("/api/v1/agent/tasks/junta/{operation_id}/result")
async def get_junta_result(operation_id: UUID) -> dict[str, object]:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    if operation.status != "completed":
        raise HTTPException(status_code=409, detail={"status": operation.status, "error": operation.error})
    return {"status": "completed", "operationId": str(operation_id), "count": len(operation.tasks), **(operation.result or {})}


@router.delete("/api/v1/agent/tasks/junta/{operation_id}")
async def cancel_junta_operation(operation_id: UUID) -> dict[str, object]:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    cancelled = await cancel_operation(operation_id)
    return {"operationId": str(operation_id), "status": "cancelled" if cancelled else operation.status}
