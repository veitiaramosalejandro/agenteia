from __future__ import annotations

from app.redis_runtime import redis_client

import json
import threading
from datetime import datetime
from typing import Any, Optional

import redis

from app.config import settings


_redis = redis_client(settings.REDIS_URL, decode_responses=True)
_fallback_lock = threading.Lock()
_fallback: dict[str, dict[str, Any]] = {}

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


def localize(data: dict[str, Any], language: str) -> dict[str, Any]:
    localized = json.loads(json.dumps(data, ensure_ascii=False))
    lang = language if language in {"es", "en", "pt"} else "pt"
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


def utc_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def aggregate_agent_status(agents: list[dict[str, Any]]) -> tuple[str, int, Optional[str]]:
    """Distinguish accepted delivery jobs from actual delivered responses."""
    states = [item.get("status") for item in agents]
    delivered = states.count("completed")
    for active in ("sending", "thinking", "searching", "processing", "queued"):
        if active in states:
            return active, delivered, None
    if "failed" in states:
        return "failed", delivered, "Uno o más agentes no pudieron responder."
    if "cancelled" in states:
        return "cancelled", delivered, None
    return "completed", delivered, None


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
    except redis.RedisError:
        with _fallback_lock:
            _fallback[request_id] = dict(data)


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


def create(request_id: str, chat_id: str, candidate_count: int) -> dict[str, Any]:
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
) -> None:
    if not request_id or (data := load(request_id)) is None:
        return
    now = utc_timestamp()
    messages = display_messages(status_name)
    if agent_resource_id:
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
    if len(data.get("agents") or []) > 1 and (
        agent_resource_id or status_name in {"completed", "failed"}
    ):
        status_name, response_count, error = aggregate_agent_status(data["agents"])
        messages = display_messages(status_name)
    completed = status_name in {"completed", "failed", "cancelled"}
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
