"""Validation helpers for separating tool envelopes from user-facing answers."""

from __future__ import annotations

import json
import re


_RAW_PREFIXES = (
    re.compile(r"^status\s*=\s*\d{3}\b", re.IGNORECASE),
    re.compile(r"^method\s*=\s*(?:get|post|put|patch|delete)\b", re.IGNORECASE),
    re.compile(r"^body\s*=\s*[\[{]", re.IGNORECASE),
    re.compile(r"^traceback\s*\(most recent call last\)", re.IGNORECASE),
    re.compile(r"^validation error\s*:", re.IGNORECASE),
)


def looks_like_raw_tool_response(response_text: str) -> bool:
    """Return True only for a transport/tool envelope, not technical prose.

    Model answers frequently contain Docker, HTTP, SQL or YAML.  Searching for
    isolated words such as ``status=`` or ``localhost`` therefore produces
    false positives.  Raw tool output must either start with an envelope
    prefix or be a JSON object carrying transport fields.
    """
    text = str(response_text or "").strip()
    if not text:
        return False

    if any(pattern.search(text) for pattern in _RAW_PREFIXES):
        return True

    if text.startswith(("{", "[")):
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            keys = {str(key).strip().lower() for key in payload}
            if keys.intersection({"status", "body", "endpoint", "tool", "tool_name"}):
                return True

    return False
