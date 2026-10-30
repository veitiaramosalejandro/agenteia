from __future__ import annotations

import json

from fastapi import APIRouter, Body

from app.api.schemas.task_junta import TaskJuntaData


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
    print("Received tasks:", request)

    print(
        "Received tasks:",
        json.dumps(
            [task.model_dump(mode="json") for task in request],
            ensure_ascii=False,
        ),
        flush=True,
    )

    return {
        "status": "accepted",
        "count": len(request),
        "taskIds": [str(task.IDTask) for task in request],
    }
