"""Normalización de idioma sin depender de servicios de traducción externos."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

def translate_response(text: str, *, target_language: str,
                       language_detector: Callable[[str], str] | None = None,
                       fallback_llm: Any = None, attempts: int = 2,
                       timeout_seconds: float = 15.0) -> str:
    """Traduce con el modelo asignado, sin llamar a Google Translate.

    ``attempts`` y ``timeout_seconds`` se conservan por compatibilidad con
    los llamadores existentes. La traducción externa se elimina del camino de
    ejecución para evitar rate limits, fuga de contenido y respuestas 429.
    """
    original = str(text or "")
    target = str(target_language or "").strip().lower()
    if not original or not target:
        return original
    if language_detector and language_detector(original) == target:
        return original

    candidate = original
    target_code = {"pt": "português europeu", "es": "español", "en": "English"}.get(
        target, target
    )
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
