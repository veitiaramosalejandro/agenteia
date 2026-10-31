from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Callable
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage

from app.api.schemas.task_junta import TaskJuntaData
from app.config import settings
from app.llm import bind_structured_json, create_chat_model, provider_config_from_settings
from app.llm.text import response_text


@dataclass(frozen=True)
class TaskMatch:
    left_id: UUID
    right_id: UUID
    reason: str


RUNNING_STATUS_NAMES = {
    2723: "NotReady",
    2724: "Ready",
    2725: "Ongoing",
    2731: "Suspended",
    2732: "InTest",
    2733: "InRedo",
    2726: "Completed",
    2727: "Cancelled",
    2734: "NewReady",
}


def _task_text(task: TaskJuntaData) -> str:
    short_name = (task.ShortName or "").strip()
    description = (task.Description or "").strip()
    return f"Nome: {short_name}\nDescrição: {description}".strip()


def _task_context(task: TaskJuntaData) -> dict[str, Any]:
    """Contexto auxiliar; ShortName y Description siguen siendo la fuente principal."""
    return {
        "importance": task.Importance,
        "complexity": task.Complexity,
        "priority": task.Priority,
        "progressPercentage": str(task.ProgressPercentage) if task.ProgressPercentage is not None else None,
        "runningStatus": task.RunningStatus,
        "runningStatusName": RUNNING_STATUS_NAMES.get(task.RunningStatus, "Unknown"),
    }


def _all_pairs(tasks: list[TaskJuntaData]) -> list[TaskMatch]:
    """Compara cada tarea solo con las tareas posteriores de la lista."""
    return [
        TaskMatch(left_id=tasks[index].IDTask, right_id=tasks[other].IDTask, reason="")
        for index in range(len(tasks))
        for other in range(index + 1, len(tasks))
    ]


def _match_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "leftId": {"type": "string"},
                        "rightId": {"type": "string"},
                        "reason": {"type": "string", "maxLength": 240},
                    },
                    "required": ["leftId", "rightId", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }


def _ask_model(
    tasks_by_id: dict[UUID, TaskJuntaData],
    pairs: list[TaskMatch],
    model_factory: Callable[[], Any] | None = None,
) -> list[TaskMatch]:
    if not pairs:
        return []

    config = provider_config_from_settings(settings)
    config = replace(
        config,
        temperature=0.0,
        max_output_tokens=max(config.max_output_tokens, 1536),
        max_retries=1,
    )
    model = (model_factory or (lambda: create_chat_model(config)))()
    model = bind_structured_json(model, config, _match_schema())
    valid_pairs = {
        frozenset((pair.left_id, pair.right_id)): pair for pair in pairs
    }
    matches: list[TaskMatch] = []
    system_message = SystemMessage(
        content=(
            "Compara cada pareja de tareas de forma independiente. Decide si tienen exactamente "
            "el mismo sentido de trabajo: mismo objetivo funcional, resultado esperado y alcance. "
            "La fuente principal de decisión es ShortName y Description. Usa Importance, Complexity, "
            "Priority, ProgressPercentage y RunningStatus solo como contexto auxiliar para confirmar "
            "o descartar la interpretación; no hagas merge solo porque compartan estado, prioridad "
            "o progreso. Estados posibles: NotReady=2723, Ready=2724, Ongoing=2725, Suspended=2731, "
            "InTest=2732, InRedo=2733, Completed=2726, Cancelled=2727 y NewReady=2734. "
            "No confundas palabras parecidas, pertenecer al mismo módulo, ser pruebas genéricas o "
            "tener una descripción vacía con el mismo trabajo. Si no puedes demostrar el mismo "
            "objetivo, no incluyas la pareja en matches. No uses una pareja para inferir otra. "
            "Devuelve únicamente JSON. Incluye solo IDs recibidos y razones de máximo 120 caracteres."
        )
    )

    for start in range(0, len(pairs), 4):
        batch = pairs[start : start + 4]
        payload = {
            "pairs": [
                {
                    "left": {
                        "id": str(pair.left_id),
                        "text": _task_text(tasks_by_id[pair.left_id])[:600],
                        "context": _task_context(tasks_by_id[pair.left_id]),
                    },
                    "right": {
                        "id": str(pair.right_id),
                        "text": _task_text(tasks_by_id[pair.right_id])[:600],
                        "context": _task_context(tasks_by_id[pair.right_id]),
                    },
                }
                for pair in batch
            ]
        }
        result = model.invoke(
            [system_message, HumanMessage(content=json.dumps(payload, ensure_ascii=False))]
        )
        raw = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            response_text(result).strip(),
            flags=re.IGNORECASE,
        )
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_task_similarity_output") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("matches"), list):
            raise ValueError("invalid_task_similarity_output")

        for item in decoded["matches"]:
            if not isinstance(item, dict):
                continue
            try:
                left_id = UUID(str(item.get("leftId")))
                right_id = UUID(str(item.get("rightId")))
            except (TypeError, ValueError):
                continue
            pair = valid_pairs.get(frozenset((left_id, right_id)))
            if pair is None or left_id == right_id:
                continue
            matches.append(
                TaskMatch(
                    left_id=pair.left_id,
                    right_id=pair.right_id,
                    reason=str(item.get("reason") or "Mismo objetivo funcional.")[:240],
                )
            )
    return matches


def analyze_task_similarity(tasks: list[TaskJuntaData]) -> dict[str, Any]:
    """Evalúa todas las parejas en orden, sin cierre transitivo de relaciones."""
    if len(tasks) < 2:
        return {
            "tasks": [
                {
                    "taskId": str(task.IDTask),
                    "identifiedTasks": [],
                    "mergeCandidate": False,
                    "mergeGroupIds": [],
                }
                for task in tasks
            ],
            "mergeGroups": [],
        }

    by_id = {task.IDTask: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate_task_ids")

    matches = _ask_model(by_id, _all_pairs(tasks))
    relations_by_task: dict[UUID, list[TaskMatch]] = {task.IDTask: [] for task in tasks}
    for match in matches:
        relations_by_task[match.left_id].append(match)
        relations_by_task[match.right_id].append(match)

    merge_groups = [
        {
            "mergeGroupId": f"merge-{index}",
            "taskIds": [str(match.left_id), str(match.right_id)],
        }
        for index, match in enumerate(matches, start=1)
    ]
    group_id_by_pair = {
        frozenset((match.left_id, match.right_id)): f"merge-{index}"
        for index, match in enumerate(matches, start=1)
    }

    result_tasks = []
    for task in tasks:
        identified = []
        group_ids = []
        for match in relations_by_task[task.IDTask]:
            other_id = match.right_id if match.left_id == task.IDTask else match.left_id
            other = by_id[other_id]
            pair = frozenset((task.IDTask, other_id))
            group_ids.append(group_id_by_pair[pair])
            identified.append(
                {
                    "taskId": str(other.IDTask),
                    "code": other.Code,
                    "shortName": other.ShortName,
                    "sameWork": True,
                    "reason": match.reason,
                }
            )
        result_tasks.append(
            {
                "taskId": str(task.IDTask),
                "code": task.Code,
                "shortName": task.ShortName,
                "identifiedTasks": identified,
                "mergeCandidate": bool(identified),
                "mergeGroupIds": group_ids,
            }
        )

    return {"tasks": result_tasks, "mergeGroups": merge_groups}
