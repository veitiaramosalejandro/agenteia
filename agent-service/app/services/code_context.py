"""Short-lived code artifacts used by follow-up code-review requests.

Code is conversation context, not durable business knowledge.  Keeping it in
its own Redis key makes references such as "el codigo anterior" independent of
SolidSET's per-message chat identifiers and prevents code from entering the
agent's long-term RAG collections.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
from typing import Any

from app.config import settings
from app.redis_runtime import redis_client


_REFERENCE_RE = re.compile(
    r"\b(?:el|la|ese|este|aquel)?\s*(?:c[oó]digo|codigo|code|html|script|programa)\s+"
    r"(?:anterior|previo|previa|que\s+te\s+(?:envi[eé]|mande|mand[eé])|mostrad[oa]|compartid[oa])\b|"
    r"\b(?:analiza|revisa|corrige|comprueba|encuentra|detecta)\b.{0,80}\b(?:anterior|previo|c[oó]digo|codigo|code)\b",
    re.IGNORECASE | re.DOTALL,
)

_ZERO_UUID = uuid.UUID(int=0)


def _stable_scope(metadata: dict[str, Any]) -> str:
    values = (
        metadata.get("solidset_instance_id"),
        metadata.get("agent_resource_id"),
        metadata.get("workroom_id") or metadata.get("channel_id"),
        metadata.get("requester_resource_id") or metadata.get("resource_id"),
    )
    return "|".join(str(value or "").strip().lower() for value in values)


def stable_conversation_session_id(metadata: dict[str, Any]) -> str:
    """Return a deterministic session, never using a zero/ephemeral chat ID."""
    explicit = str(metadata.get("session_id") or "").strip()
    try:
        parsed = uuid.UUID(explicit)
        if parsed != _ZERO_UUID:
            return str(parsed)
    except (ValueError, AttributeError):
        if explicit:
            return explicit

    scope = _stable_scope(metadata)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"solidset-agent-session:v2:{scope}"))


def _artifact_key(session_id: str) -> str:
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:32]
    return f"machining:code-context:v1:{digest}"


def looks_like_code(text: str) -> bool:
    value = html.unescape(str(text or "")).strip()
    if len(value) < 80:
        return False
    lowered = value.casefold()
    if "```" in value or "<!doctype html" in lowered or "<html" in lowered:
        return True
    indicators = (
        "</", "/>", "function ", "const ", "let ", "import ", "def ",
        "class ", "SELECT ", "CREATE TABLE", "=>", "{", "}",
    )
    return sum(indicator.casefold() in lowered for indicator in indicators) >= 2


def is_code_reference(text: str) -> bool:
    return bool(_REFERENCE_RE.search(str(text or "").strip()))


def _normalize_code(text: str) -> str:
    value = html.unescape(str(text or "")).strip()
    limit = settings.CODE_CONTEXT_MAX_CHARS
    if len(value) > limit:
        value = value[:limit].rstrip() + "\n[contenido de codigo recortado por limite]"
    return value


def store_code_context(session_id: str, text: str, *, chat_id: Any = None) -> dict[str, Any] | None:
    code = _normalize_code(text)
    if not session_id or not looks_like_code(code):
        return None
    record = {
        "type": "code_context",
        "content": code,
        "chat_id": str(chat_id or ""),
        "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
    }
    client = redis_client()
    try:
        client.set(
            _artifact_key(session_id),
            json.dumps(record, ensure_ascii=False),
            ex=settings.CODE_CONTEXT_TTL_SECONDS,
        )
    finally:
        client.close()
    return record


def load_code_context(session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    client = redis_client()
    try:
        raw = client.get(_artifact_key(session_id))
    finally:
        client.close()
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("content") else None


def attach_code_context(
    text: str,
    metadata: dict[str, Any] | None,
    session_id: str,
    *,
    chat_id: Any = None,
) -> dict[str, Any]:
    """Persist a code turn or attach the previous code to a follow-up turn."""
    target = metadata if metadata is not None else {}
    if target.get("code_context_source") == "current_message" and target.get("code_context"):
        return target
    if looks_like_code(text):
        record = store_code_context(session_id, text, chat_id=chat_id)
        if record:
            target["code_context"] = record["content"]
            target["code_context_sha256"] = record["sha256"]
            target["code_context_source"] = "current_message"
            target["code_reference_mode"] = True
            target["model_capability"] = "coding"
        return target

    if is_code_reference(text):
        record = load_code_context(session_id)
        if record:
            target["code_context"] = str(record["content"])
            target["code_context_sha256"] = str(record.get("sha256") or "")
            target["code_context_chat_id"] = str(record.get("chat_id") or "")
            target["code_context_source"] = "previous_message"
            target["code_reference_mode"] = True
            target["model_capability"] = "coding"
        else:
            target["code_context_missing"] = True
    return target
