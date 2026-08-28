"""Rules for keeping user-provided knowledge separate from model-generated text."""

from __future__ import annotations

import re


LEGACY_SUGGESTION_SOURCE = "chat-question:user-assertion"
USER_ASSERTION_SOURCE = "chat-question:user-assertion:v2"


def looks_like_generated_suggestion(text: str) -> bool:
    """Detect legacy drafts/assistant disclaimers that must not be treated as facts."""
    normalized = " ".join(str(text or "").strip().casefold().split())
    if not normalized:
        return True
    return bool(
        re.match(r"^(?:\d+[.)-]\s+|[-*]\s+|#{1,6}\s+)", normalized)
        or re.match(r"^\*{1,2}[^*]{2,80}\*{1,2}\s*:", normalized)
        or re.search(
            r"\b(?:desculpe(?:-me)?|lo siento|i(?:'m| am) sorry|"
            r"n[aã]o tenho informa[cç][oõ]es|no tengo informaci[oó]n|"
            r"i do not have information|poderia(?:mos)? procurar|podemos buscar|"
            r"consulte|verifique|entre em contacto|contacte con|"
            r"[eé] importante notar|es importante señalar|"
            r"pesquisas espec[ií]ficas podem ser necess[aá]rias)\b",
            normalized,
        )
    )


def usable_agent_knowledge(text: str, source: str) -> bool:
    """Accept manual/current assertions and conservatively filter legacy rows."""
    normalized = " ".join(str(text or "").strip().casefold().split())
    if re.match(
        r"^(?:investiga|investigar|investigue|pesquisa|pesquisar|pesquise|research|"
        r"busca|buscar|averigua|analiza|analise|explica|dime|responde|haz|faça)\b",
        normalized,
    ):
        return False
    if str(source or "").strip() != LEGACY_SUGGESTION_SOURCE:
        return bool(normalized)
    return not looks_like_generated_suggestion(text)
