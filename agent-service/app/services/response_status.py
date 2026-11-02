from __future__ import annotations

from app.redis_runtime import redis_client

import json
import threading
import time
from datetime import datetime
from typing import Any, Optional

import redis

from app.config import settings


_redis = redis_client(settings.REDIS_URL, decode_responses=True)
_fallback_lock = threading.Lock()
_fallback: dict[str, dict[str, Any]] = {}
_fallback_agent_index: dict[str, dict[str, float]] = {}

DISPLAY_MESSAGES = {
    "queued": {"es": "Esperando…", "en": "Waiting…", "pt": "A aguardar…"},
    "processing": {"es": "Procesando…", "en": "Processing…", "pt": "A processar…"},
    "searching": {
        "es": "Buscando información…",
        "en": "Searching for information…",
        "pt": "A pesquisar informação…",
    },
    "thinking": {"es": "Pensando…", "en": "Thinking…", "pt": "A pensar…"},
    "sending": {
        "es": "Enviando respuesta…",
        "en": "Sending response…",
        "pt": "A enviar a resposta…",
    },
    "completed": {"es": "Respondido", "en": "Answered", "pt": "Respondido"},
    "learned": {"es": "Aprendido", "en": "Learned", "pt": "Aprendido"},
    "failed": {
        "es": "No se pudo responder",
        "en": "Unable to respond",
        "pt": "Não foi possível responder",
    },
    "cancelled": {"es": "Cancelado", "en": "Cancelled", "pt": "Cancelado"},
}
CODES = {
    name: index
    for index, name in enumerate(
        (
            "queued",
            "processing",
            "searching",
            "thinking",
            "sending",
            "completed",
            "learned",
            "failed",
            "cancelled",
        )

    )
}


def display_messages(status_name: str) -> dict[str, str]:
    return dict(
        DISPLAY_MESSAGES.get(status_name)
        or {"es": status_name, "en": status_name, "pt": status_name}
    )


def localize(data: dict[str, Any], language: str | None = None) -> dict[str, Any]:
    localized = json.loads(json.dumps(data, ensure_ascii=False))
    result_language = ((localized.get("result") or {}).get("language")
                       if isinstance(localized.get("result"), dict) else None)
    lang = language if language in {"es", "en", "pt"} else (
        result_language if result_language in {"es", "en", "pt"} else "pt"
    )
    messages = display_messages(str(localized.get("status") or ""))
    localized.update(
        code=CODES.get(str(localized.get("status") or ""), -1),
        displayMessages=messages,
        displayMessage=messages[lang],
        language=lang,
    )
    for state in localized.get("agents") or []:
        messages = display_messages(str(state.get("status") or ""))
        state.update(
            code=CODES.get(str(state.get("status") or ""), -1),
            displayMessages=messages,
            displayMessage=messages[lang],
        )
    return localized


def _key(request_id: str) -> str:
    return f"machining:agent-response:v1:{request_id}"


def _chat_key(chat_id: str) -> str:
    return f"machining:agent-response-chat:v1:{chat_id}"


def _agent_index_key(agent_resource_id: str) -> str:
    return f"machining:agent-response-agent:v1:{agent_resource_id}"


def utc_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def aggregate_agent_status(agents: list[dict[str, Any]]) -> tuple[str, int, Optional[str]]:
    """Distinguish accepted delivery jobs from actual delivered responses."""
    states = [item.get("status") for item in agents]
    delivered = states.count("completed")
    learned = states.count("learned")
    for active in ("sending", "thinking", "searching", "processing", "queued"):
        if active in states:
            return active, delivered + learned, None
    if "failed" in states:
        return "failed", delivered + learned, "Uno o más agentes no pudieron responder."
    if "cancelled" in states:
        return "cancelled", delivered + learned, None
    if "completed" in states:
        return "completed", delivered + learned, None
    return "learned", learned, None


def save(data: dict[str, Any]) -> None:

    request_id = str(data["requestId"])
    try:
        _redis.setex(
            _key(request_id),
            settings.AGENT_RESPONSE_STATUS_TTL_SECONDS,
            json.dumps(data, ensure_ascii=False),
        )
        if chat_id := str(data.get("chatId") or "").strip():
            _redis.setex(
                _chat_key(chat_id),
                settings.AGENT_RESPONSE_STATUS_TTL_SECONDS,
                request_id,
            )
        agent_ids = {
            str(data.get("agentResourceId") or "").strip(),
            *(
                str(item.get("agentResourceId") or "").strip()
                for item in data.get("agents") or []
            ),
        }
        for agent_id in filter(None, agent_ids):
            index_key = _agent_index_key(agent_id)
            _redis.zadd(index_key, {request_id: time.time()})
            _redis.expire(index_key, settings.AGENT_RESPONSE_STATUS_TTL_SECONDS)
    except redis.RedisError:
        with _fallback_lock:
            _fallback[request_id] = dict(data)
            for agent_id in {
                str(data.get("agentResourceId") or "").strip(),
                *(
                    str(item.get("agentResourceId") or "").strip()
                    for item in data.get("agents") or []
                ),
            }:
                if agent_id:
                    _fallback_agent_index.setdefault(agent_id, {})[request_id] = time.time()


def load(request_id: str) -> Optional[dict[str, Any]]:
    try:
        raw = _redis.get(_key(request_id))
        return json.loads(raw) if raw else None
    except (redis.RedisError, json.JSONDecodeError):
        with _fallback_lock:
            value = _fallback.get(request_id)
            return dict(value) if value else None


def load_by_chat(chat_id: str) -> Optional[dict[str, Any]]:
    try:
        request_id = _redis.get(_chat_key(chat_id))
    except redis.RedisError:
        with _fallback_lock:
            request_id = next(
                (
                    key
                    for key, value in reversed(list(_fallback.items()))
                    if str(value.get("chatId") or "") == chat_id
                ),
                None,
            )
    return load(str(request_id)) if request_id else None


def create(
    request_id: str,
    chat_id: str,
    candidate_count: int,
    question: Optional[str] = None,
) -> dict[str, Any]:
    now = utc_timestamp()
    data = {
        "requestId": request_id,
        "chatId": chat_id or None,
        "status": "queued",
        "code": CODES["queued"],
        "displayMessage": DISPLAY_MESSAGES["queued"]["pt"],
        "displayMessages": display_messages("queued"),
        "completed": False,
        "createdAt": now,
        "updatedAt": now,
        "completedAt": None,
        "candidateCount": candidate_count,
        "responseCount": 0,
        "error": None,
        "agents": [],
        "stageHistory": [{"status": "queued", "at": now}],
    }
    if question and question.strip():
        data["question"] = question.strip()
    save(data)
    return data


def update(
    request_id: str,
    status_name: str,
    *,
    agent_resource_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    error: Optional[str] = None,
    response_count: Optional[int] = None,
    result: Optional[dict[str, Any]] = None,
    effective_model: Optional[dict[str, Any]] = None,
) -> None:
    if not request_id or (data := load(request_id)) is None:
        return
    now = utc_timestamp()
    messages = display_messages(status_name)
    if agent_resource_id:
        data["agentResourceId"] = agent_resource_id
        if agent_name:
            data["agentName"] = agent_name
        agents = data.setdefault("agents", [])
        state = next(
            (
                item
                for item in agents
                if item.get("agentResourceId") == agent_resource_id
            ),
            None,
        )
        if state is None:
            state = {"agentResourceId": agent_resource_id, "name": agent_name or ""}
            agents.append(state)
        state.update(
            status=status_name,
            code=CODES.get(status_name, -1),
            displayMessage=messages["pt"],
            displayMessages=messages,
            updatedAt=now,
            error=error,
        )
        if effective_model:
            state["effectiveModel"] = dict(effective_model)
    if len(data.get("agents") or []) > 1 and (
        agent_resource_id or status_name in {"completed", "failed"}
    ):
        status_name, response_count, error = aggregate_agent_status(data["agents"])
        messages = display_messages(status_name)
    completed = status_name in {"completed", "failed", "cancelled", "learned"}
    data.update(
        status=status_name,

        code=CODES.get(status_name, -1),
        displayMessage=messages["pt"],
        displayMessages=messages,
        updatedAt=now,
        completed=completed,
        error=error,
        completedAt=now if completed else None,
    )
    if response_count is not None:
        data["responseCount"] = response_count
    if result is not None:
        data["result"] = result
    history = data.setdefault("stageHistory", [])
    if not history or history[-1].get("status") != status_name:
        history.append(
            {"status": status_name, "at": now, "agentResourceId": agent_resource_id}
        )
    save(data)


def list_by_agent(agent_resource_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent response states associated with one agent resource."""
    agent_id = str(agent_resource_id or "").strip()
    bounded_limit = max(1, min(int(limit), 200))
    if not agent_id:
        return []
    try:
        request_ids = _redis.zrevrange(_agent_index_key(agent_id), 0, bounded_limit - 1)
        indexed_ids = {str(request_id) for request_id in request_ids}
        # Backfill statuses created before the per-agent index existed. This
        # keeps the history view useful immediately after deployment while the
        # normal path remains a sorted-set lookup.
        if len(indexed_ids) < bounded_limit:
            for key in _redis.scan_iter(match="machining:agent-response:v1:*"):
                request_id = str(key).rsplit(":", 1)[-1]
                if request_id in indexed_ids:
                    continue
                data = load(request_id)
                agent_ids = {
                    str(data.get("agentResourceId") or "").strip(),
                    *(str(item.get("agentResourceId") or "").strip() for item in (data or {}).get("agents") or []),
                } if data else set()
                if data is not None and agent_id in agent_ids:
                    indexed_ids.add(request_id)
                    _redis.zadd(_agent_index_key(agent_id), {request_id: time.time()})
        request_ids = list(indexed_ids)
        result = []
        for request_id in request_ids:
            data = load(str(request_id))
            if data is not None:
                result.append(data)
        result.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        return result[:bounded_limit]
    except redis.RedisError:
        with _fallback_lock:
            entries = sorted(
                _fallback_agent_index.get(agent_id, {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:bounded_limit]
            return [dict(_fallback[request_id]) for request_id, _ in entries if request_id in _fallback]


def prepare_retry(request_id: str, error: str) -> None:
    """Reset failures from a previous attempt without creating phantom agents."""
    data = load(request_id)
    if data is None:
        return
    data["agents"] = [
        state for state in data.get("agents") or []
        if state.get("status") != "failed"
    ]
    now = utc_timestamp()
    messages = display_messages("queued")
    data.update(
        status="queued",
        code=CODES["queued"],
        displayMessage=messages["pt"],
        displayMessages=messages,
        updatedAt=now,
        completed=False,
        completedAt=None,
        error=error,
    )
    data.pop("result", None)
    data.setdefault("stageHistory", []).append({"status": "queued", "at": now})
    save(data)
