from __future__ import annotations

import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import OllamaEmbeddings

from app.api.schemas.task_junta import TaskJuntaData
from app.config import settings
from app.llm import bind_structured_json, create_chat_model, provider_config_from_settings
from app.llm.text import response_text


@dataclass(frozen=True)
class TaskMatch:
    left_id: UUID
    right_id: UUID
    candidate_score: float
    reason: str


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _task_text(task: TaskJuntaData) -> str:
    short_name = (task.ShortName or "").strip()
    description = (task.Description or "").strip()
    return f"Nome: {short_name}\nDescrição: {description}".strip()


def _candidate_pairs(
    tasks: list[TaskJuntaData],
    vectors: list[list[float]],
    threshold: float,
    top_k: int,
) -> list[TaskMatch]:
    candidates: list[TaskMatch] = []
    for index, task in enumerate(tasks):
        nearest: list[tuple[float, int]] = []
        for other_index in range(len(tasks)):
            if index == other_index:
                continue
            score = _cosine(vectors[index], vectors[other_index])
            if score >= threshold:
                nearest.append((score, other_index))
        for score, other_index in sorted(nearest, reverse=True)[:top_k]:
            if task.IDTask.int >= tasks[other_index].IDTask.int:
                continue
            candidates.append(
                TaskMatch(
                    left_id=task.IDTask,
                    right_id=tasks[other_index].IDTask,
                    candidate_score=round(score, 4),
                    reason="",
                )
            )
    return candidates


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
                        "reason": {"type": "string", "maxLength": 500},
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
    candidates: list[TaskMatch],
    model_factory: Callable[[], Any] | None = None,
) -> list[TaskMatch]:
    if not candidates:
        return []

    config = provider_config_from_settings(settings)
    config = replace(
        config,
        temperature=0.0,
        max_output_tokens=min(max(config.max_output_tokens, 1024), 4096),
        max_retries=1,
    )
    model = (model_factory or (lambda: create_chat_model(config)))()
    model = bind_structured_json(model, config, _match_schema())

    candidate_by_pair = {
        frozenset((candidate.left_id, candidate.right_id)): candidate
        for candidate in candidates
    }
    validated: list[TaskMatch] = []
    system_message = SystemMessage(
        content=(
            "Analiza parejas de tareas y decide si tienen el mismo sentido de trabajo: "
            "si persiguen el mismo objetivo funcional, resultado y alcance, aunque usen "
            "palabras diferentes. No marques una pareja solo porque comparta palabras, módulo "
            "o tema. La puntuación candidateScore solo sirve para priorizar la revisión y "
            "nunca es una decisión. Devuelve únicamente JSON con matches. Incluye solo parejas "
            "que realmente podrían representar una única tarea funcional; no inventes IDs. "
            "La respuesta debe estar en español y cada reason debe ser breve."
        )
    )
    for start in range(0, len(candidates), 20):
        batch = candidates[start : start + 20]
        candidate_payload = [
            {
                "left": {"id": str(candidate.left_id), "text": _task_text(tasks_by_id[candidate.left_id])[:900]},
                "right": {"id": str(candidate.right_id), "text": _task_text(tasks_by_id[candidate.right_id])[:900]},
                "candidateScore": candidate.candidate_score,
            }
            for candidate in batch
        ]
        result = model.invoke(
            [
                system_message,
                HumanMessage(content=json.dumps({"candidates": candidate_payload}, ensure_ascii=False)),
            ]
        )
        raw = response_text(result).strip()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            raise ValueError("invalid_task_similarity_output")
        for item in payload["matches"]:
            if not isinstance(item, dict):
                continue
            try:
                left_id = UUID(str(item.get("leftId")))
                right_id = UUID(str(item.get("rightId")))
            except (TypeError, ValueError):
                continue
            if left_id == right_id or left_id not in tasks_by_id or right_id not in tasks_by_id:
                continue
            candidate = candidate_by_pair.get(frozenset((left_id, right_id)))
            if candidate is None:
                continue
            validated.append(
                TaskMatch(
                    left_id=candidate.left_id,
                    right_id=candidate.right_id,
                    candidate_score=candidate.candidate_score,
                    reason=str(item.get("reason") or "Tarefas com objetivo e alcance relacionados.")[:500],
                )
            )
    return validated


def analyze_task_similarity(tasks: list[TaskJuntaData]) -> dict[str, Any]:
    """Identifica tarefas relacionadas sem executar agentes nem alterar SolidSET."""
    if len(tasks) < 2:
        return {
            "tasks": [
                {"taskId": str(task.IDTask), "identifiedTasks": [], "mergeCandidate": False}
                for task in tasks
            ],
            "mergeGroups": [],
        }

    by_id = {task.IDTask: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate_task_ids")

    embeddings = OllamaEmbeddings(
        base_url=settings.EMBEDDING_BASE_URL,
        model=settings.EMBEDDING_MODEL_NAME,
    )
    vectors = embeddings.embed_documents([_task_text(task) for task in tasks])
    threshold = float(os.getenv("TASK_SIMILARITY_EMBEDDING_THRESHOLD", "0.50"))
    top_k = max(1, min(int(os.getenv("TASK_SIMILARITY_TOP_K", "8")), 20))
    candidates = _candidate_pairs(tasks, vectors, threshold, top_k)
    matches = _ask_model(by_id, candidates)

    adjacency: dict[UUID, set[UUID]] = defaultdict(set)
    reason_by_pair: dict[frozenset[UUID], str] = {}
    for match in matches:
        pair = frozenset((match.left_id, match.right_id))
        adjacency[match.left_id].add(match.right_id)
        adjacency[match.right_id].add(match.left_id)
        reason_by_pair[pair] = match.reason

    groups: list[list[UUID]] = []
    visited: set[UUID] = set()
    for root in adjacency:
        if root in visited:
            continue
        component: list[UUID] = []
        queue = deque([root])
        visited.add(root)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)
        if len(component) > 1:
            groups.append(sorted(component, key=lambda value: str(value)))

    group_by_task: dict[UUID, str] = {}
    for index, group in enumerate(groups, start=1):
        group_id = f"merge-{index}"
        for task_id in group:
            group_by_task[task_id] = group_id

    response_tasks = []
    for task in tasks:
        identified = []
        for other_id in sorted(adjacency.get(task.IDTask, set()), key=lambda value: str(value)):
            pair = frozenset((task.IDTask, other_id))
            other = by_id[other_id]
            identified.append(
                {
                    "taskId": str(other.IDTask),
                    "code": other.Code,
                    "shortName": other.ShortName,
                    "sameWork": True,
                    "reason": reason_by_pair.get(pair, ""),
                }
            )
        response_tasks.append(
            {
                "taskId": str(task.IDTask),
                "code": task.Code,
                "shortName": task.ShortName,
                "identifiedTasks": identified,
                "mergeCandidate": bool(identified),
                "mergeGroupId": group_by_task.get(task.IDTask),
            }
        )

    return {
        "tasks": response_tasks,
        "mergeGroups": [
            {"mergeGroupId": f"merge-{index}", "taskIds": [str(task_id) for task_id in group]}
            for index, group in enumerate(groups, start=1)
        ],
    }
