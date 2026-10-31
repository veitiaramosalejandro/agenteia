from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.api.schemas.task_junta import TaskJuntaData
from app.services.task_similarity import analyze_task_similarity


router = APIRouter(tags=["Tasks"])


@router.post("/api/v1/agent/tasks/junta")
def receive_junta_tasks(
    request: list[TaskJuntaData] = Body(..., max_length=1000),
) -> dict[str, object]:
    """Recibe una lista de tareas Junta y devuelve los identificadores aceptados.

    La recepción no persiste ni actualiza tareas todavía; esa operación debe
    definirse explícitamente para evitar sobrescribir datos de SysTask por
    accidente.
    """
    try:
        result = analyze_task_similarity(request)
    except ValueError as exc:
        if str(exc) == "invalid_task_similarity_output":
            raise HTTPException(
                status_code=502,
                detail="El modelo devolvió una respuesta JSON incompleta o inválida.",
            ) from None
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        print(f"TASK_JUNTA_ANALYSIS_FAILED type={type(exc).__name__}", flush=True)
        raise HTTPException(status_code=503, detail="No se pudo analizar la similitud de las tareas.") from None

    return {"status": "analyzed", "count": len(request), **result}
