"""Traducción de respuestas usando siempre Google Translate."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

_GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
_GOOGLE_LANGUAGE_CODES = {"pt": "pt-PT", "es": "es", "en": "en"}


def translate_response(text: str, *, target_language: str,
                       language_detector: Callable[[str], str] | None = None,
                       fallback_llm: Any = None, attempts: int = 2,
                       timeout_seconds: float = 15.0) -> str:
    """Traduce una respuesta mediante Google Translate con fallback seguro."""
    original = str(text or "")
    target = str(target_language or "").strip().lower()
    if not original or not target or attempts < 1:
        return original
    if language_detector and language_detector(original) == target:
        return original

    target_code = _GOOGLE_LANGUAGE_CODES.get(target, target)
    candidate = original
    for _ in range(max(1, min(attempts, 3))):
        try:
            protected_parts: list[str] = []

            def protect(match: re.Match[str]) -> str:
                protected_parts.append(match.group(0))
                return f" __SOLIDSET_TOKEN_{len(protected_parts) - 1}__ "

            protected = re.sub(
                r"```[\s\S]*?```|`[^`]*`|https?://[^\s)]+",
                protect,
                candidate,
            )
            query = urllib.parse.urlencode({
                "client": "gtx", "sl": "auto", "tl": target_code,
                "dt": "t", "q": protected,
            })
            request = urllib.request.Request(
                f"{_GOOGLE_TRANSLATE_URL}?{query}",
                headers={"User-Agent": "SolidSET-agent-service/1.0"},
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            translated = "".join(part[0] for part in payload[0] if part and part[0])
            for index, value in enumerate(protected_parts):
                translated = translated.replace(f"__SOLIDSET_TOKEN_{index}__", value)
            if translated.strip():
                candidate = translated.strip()
            if not language_detector or language_detector(candidate) == target:
                return candidate
        except Exception as exc:  # Un fallo de traducción no bloquea la respuesta.
            print(f"⚠️ Google Translate failed: {exc}")
    if fallback_llm is not None:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            fallback = fallback_llm.invoke([
                SystemMessage(content=(
                    f"Translate the supplied response entirely to {target_code}. "
                    "Do not answer the original question. Preserve names, figures, "
                    "dates, Markdown, URLs and technical identifiers exactly. "
                    "Return only the translated text."
                )),
                HumanMessage(content=original),
            ])
            fallback_text = getattr(fallback, "content", fallback)
            if isinstance(fallback_text, list):
                fallback_text = "".join(
                    str(item.get("text", "")) for item in fallback_text
                    if isinstance(item, dict)
                )
            fallback_text = str(fallback_text or "").strip()
            original_numbers = set(re.findall(r"(?<!\w)[-+]?\d+(?:[.,]\d+)?", original))
            fallback_numbers = set(re.findall(r"(?<!\w)[-+]?\d+(?:[.,]\d+)?", fallback_text))
            # El modelo local es solo un traductor de emergencia. Rechaza una
            # respuesta que haya resumido, inventado o contestado de nuevo.
            content_preserved = len(fallback_text) >= max(8, int(len(original) * 0.45))
            numbers_preserved = original_numbers.issubset(fallback_numbers)
            language_preserved = (
                not language_detector or language_detector(fallback_text) == target
            )
            if fallback_text and content_preserved and numbers_preserved and language_preserved:
                return fallback_text
            print(
                "⚠️ Local translation fallback rejected: "
                f"content={content_preserved} numbers={numbers_preserved} "
                f"language={language_preserved}"
            )
        except Exception as exc:
            print(f"⚠️ Local translation fallback failed: {exc}")
    return candidate or original
