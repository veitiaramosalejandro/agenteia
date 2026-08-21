from __future__ import annotations

import hashlib
import html
import re
from typing import Any

SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|contraseña|senha|api[_ -]?key|token|secret)\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)


def normalize_historical_message(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    raw = str(row.get("RawMessage") or "").strip()
    if not raw:
        return None, "empty"
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = " ".join(text.replace("\x00", " ").split()).strip()
    if not text:
        return None, "empty"
    if bool(row.get("GeneratedByIA")) or text.lower().startswith("asistente ia "):
        return None, "generated_by_ia"
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        return None, "sensitive"
    if len(text) < 2:
        return None, "technical"
    sender_resource = str(row.get("IDSenderResource") or "").strip()
    workroom_id = str(row.get("IDWorkRoom") or "").strip()
    if not sender_resource or not workroom_id:
        return None, "invalid_participants"
    normalized = dict(row)
    normalized["NormalizedText"] = text[:12000]
    normalized["ContentHash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return normalized, None
