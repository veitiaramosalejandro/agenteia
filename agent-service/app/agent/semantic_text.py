"""Deterministic semantic signals used before invoking an LLM.

The helpers deliberately operate on language structure and intent families,
not on individual people, countries, products, or expected answers.
"""

from __future__ import annotations

import re
import unicodedata


def normalized_text(value: object) -> str:
    """Return lowercase, accent-free text with stable word boundaries."""
    raw = unicodedata.normalize("NFKD", str(value or "").casefold())
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", raw))


def compact_text(value: object) -> str:
    """Return a boundary-free form tolerant of omitted spaces and hyphens."""
    return normalized_text(value).replace(" ", "")


_LANGUAGE_SIGNALS = {
    # Function words are more reusable and less topic-dependent than nouns.
    "es": {
        "quien", "donde", "cuando", "cuanto", "cual", "como", "porque",
        "puedo", "quiero", "necesito", "tengo", "del", "los", "las",
        "tarea", "hacer", "investiga", "investigar", "debo",
    },
    "pt": {
        "quem", "onde", "quando", "quanto", "qual", "como", "porque",
        "posso", "quero", "preciso", "tenho", "dos", "das", "uma", "nao",
        "tarefa", "fazer", "pesquisa", "pesquisar", "devo",
    },
    "en": {
        "who", "where", "when", "which", "what", "how", "why", "can",
        "want", "need", "have", "this", "that", "the", "from", "with",
    },
}


def language_signal(value: object) -> tuple[str, int]:
    """Return an unambiguous language inferred from current-request grammar."""
    words = set(normalized_text(value).split())
    scores = {
        language: len(words.intersection(signals))
        for language, signals in _LANGUAGE_SIGNALS.items()
    }
    best_score = max(scores.values(), default=0)
    winners = [language for language, score in scores.items() if score == best_score]
    if best_score == 0 or len(winners) != 1:
        return "", best_score
    return winners[0], best_score


_QUESTION_START = re.compile(
    r"^(?:quem|quien|qual|cual|what|which|who)\b"
)

# These are role families, rather than names of current officeholders. Matching
# their compact form makes the rule tolerate ``primeiroministro``,
# ``primeiro-ministro`` and ``primeiro ministro`` equally.
_CURRENT_ROLE_FORMS = tuple(
    compact_text(role)
    for role in (
        "presidente", "president", "primeiro ministro", "primer ministro",
        "prime minister", "governador", "gobernador", "governor", "prefeito",
        "alcalde", "mayor", "ceo", "diretor executivo", "director ejecutivo",
        "chief executive officer",
    )
)


def is_current_officeholder_question(value: object) -> bool:
    """Recognize multilingual questions asking who currently holds a role."""
    normalized = normalized_text(value)
    if not _QUESTION_START.match(normalized):
        return False
    compact = compact_text(normalized)
    return any(role in compact for role in _CURRENT_ROLE_FORMS)
