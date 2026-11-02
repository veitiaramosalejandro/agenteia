from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.api.schemas.task_junta import TaskJuntaData
from app.services.task_similarity import TaskMatch, analyze_task_similarity

logger = logging.getLogger(__name__)


@dataclass
class JuntaOperation:
    operation_id: UUID
    instance: str
    work_room_id: UUID
    id_login: UUID
    id_resource: UUID
    id_session: UUID
    notification_url: str
    tasks: list[TaskJuntaData]
    task_set_version: str | None = None
    payload_hash: str | None = None
    status: str = "accepted"
    result: dict[str, Any] | None = None
    error: str | None = None
    sequence: int = 0
    processed_pairs: int = 0
    total_pairs: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    task: asyncio.Task | None = field(default=None, repr=False)


_operations: dict[UUID, JuntaOperation] = {}
_lock = asyncio.Lock()


def _hash_payload(request: Any) -> str:
    raw = json.dumps(request, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _event_body(operation: JuntaOperation, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    operation.sequence += 1
    return {
        "operationId": str(operation.operation_id),
        "eventId": str(uuid4()),
        "sequence": operation.sequence,
        "eventType": event_type,
        "instance": operation.instance,
        "workRoomId": str(operation.work_room_id),
        "idLogin": str(operation.id_login),
        "idResource": str(operation.id_resource),
        **payload,
    }


async def _publish(operation: JuntaOperation, event_type: str, payload: dict[str, Any]) -> None:
    url = str(operation.notification_url or "").strip()
    if not url:
        logger.warning("JUNTA_EVENT_NOT_PUBLISHED reason=notification_url_missing operation_id=%s", operation.operation_id)
        return

    body = _event_body(operation, event_type, payload)
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    secret = str(getattr(settings, "JUNTA_NOTIFICATION_SHARED_SECRET", "") or "")
    timestamp = str(int(time.time()))
    # Debe coincidir con NotificationManager.JuntaEventSecurity:
    # HMAC-SHA256(timestamp + "." + body), enviado en los encabezados X-Junta-*.
    signature = hmac.new(secret.encode("utf-8"), f"{timestamp}.{encoded}".encode("utf-8"), hashlib.sha256).hexdigest() if secret else ""
    headers = {
        "Content-Type": "application/json",
        "X-Junta-Timestamp": timestamp,
        "X-Junta-Signature": signature,
    }
    timeout = max(2, int(getattr(settings, "JUNTA_EVENT_TIMEOUT_SECONDS", 10)))
    retries = max(0, int(getattr(settings, "JUNTA_EVENT_MAX_RETRIES", 3)))
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 5))) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.post(url, content=encoded, headers=headers)
                if response.status_code in {200, 201, 202, 204}:
                    return
                if response.status_code < 500 and response.status_code != 429:
                    raise RuntimeError(f"notification_rejected_{response.status_code}")
                raise RuntimeError(f"notification_retryable_{response.status_code}")
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"junta_event_delivery_failed: {last_error}") from last_error


def _match_payload(match: TaskMatch) -> dict[str, Any]:
    return {"taskId": str(match.left_id), "matchedTaskId": str(match.right_id), "reason": match.reason}


async def _run(operation: JuntaOperation) -> None:
    operation.status = "running"
    operation.total_pairs = len(operation.tasks) * max(0, len(operation.tasks) - 1) // 2
    loop = asyncio.get_running_loop()
    pending_publications: list[Any] = []
    try:
        await _publish(operation, "started", {
            "taskCount": len(operation.tasks), "processedPairs": 0,
            "totalPairs": operation.total_pairs, "percent": 0,
        })

        def on_batch(processed: int, total: int, matches: list[TaskMatch]) -> None:
            operation.processed_pairs = processed
            operation.total_pairs = total
            for match in matches:
                pending_publications.append(asyncio.run_coroutine_threadsafe(
                    _publish(operation, "match", _match_payload(match)), loop
                ))
            percent = round((processed / total) * 100, 2) if total else 100
            pending_publications.append(asyncio.run_coroutine_threadsafe(
                _publish(operation, "progress", {
                    "processedPairs": processed, "totalPairs": total, "percent": percent,
                }), loop
            ))

        operation.result = await asyncio.to_thread(analyze_task_similarity, operation.tasks, on_batch)
        if pending_publications:
            await asyncio.gather(*(
                asyncio.wrap_future(future, loop=loop)
                for future in pending_publications
            ))
        operation.status = "completed"
        await _publish(operation, "completed", {
            "resultUrl": f"/api/v1/agent/tasks/junta/{operation.operation_id}/result",
            "matchCount": len(operation.result.get("mergeGroups", [])),
            "processedPairs": operation.total_pairs, "totalPairs": operation.total_pairs, "percent": 100,
        })
    except asyncio.CancelledError:
        operation.status = "cancelled"
        operation.error = "operation_cancelled"
        with contextlib.suppress(Exception):
            await _publish(operation, "failed", {"error": operation.error})
        raise
    except Exception as exc:
        operation.status = "failed"
        operation.error = type(exc).__name__
        logger.exception("TASK_JUNTA_OPERATION_FAILED operation_id=%s", operation.operation_id)
        with contextlib.suppress(Exception):
            await _publish(operation, "failed", {"error": "analysis_failed"})


async def create_operation(
    *, operation_id: UUID, instance: str, work_room_id: UUID, id_login: UUID,
    id_resource: UUID, id_session: UUID, notification_url: str,
    tasks: list[TaskJuntaData],
    task_set_version: str | None = None, payload_hash: str | None = None,
) -> JuntaOperation:
    async with _lock:
        existing = _operations.get(operation_id)
        if existing:
            return existing
        operation = JuntaOperation(
            operation_id=operation_id, instance=instance, work_room_id=work_room_id,
            id_login=id_login, id_resource=id_resource, id_session=id_session,
            notification_url=notification_url,
            tasks=tasks, task_set_version=task_set_version,
            payload_hash=payload_hash or _hash_payload(tasks),
        )
        _operations[operation_id] = operation
        operation.task = asyncio.create_task(_run(operation), name=f"junta-{operation_id}")
        return operation


def get_operation(operation_id: UUID) -> JuntaOperation | None:
    return _operations.get(operation_id)


async def cancel_operation(operation_id: UUID) -> bool:
    operation = _operations.get(operation_id)
    if not operation or not operation.task or operation.task.done():
        return False
    operation.task.cancel()
    return True
