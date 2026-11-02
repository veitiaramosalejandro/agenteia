"""Authoritative SolidSET task running-status catalog."""
from __future__ import annotations

import re
from typing import Any

SYS_TASK_RUNNING_STATUS = {
    2723: {"es": "No preparada", "pt": "Não preparada", "en": "Not ready"},
    2724: {"es": "Preparada", "pt": "Preparada", "en": "Ready"},
    2725: {"es": "En curso", "pt": "Em curso", "en": "Ongoing"},
    2731: {"es": "Suspendida", "pt": "Suspensa", "en": "Suspended"},
    2732: {"es": "En pruebas", "pt": "Em teste", "en": "In test"},
    2733: {"es": "En corrección", "pt": "Em correção", "en": "In redo"},
    2726: {"es": "Completada", "pt": "Concluída", "en": "Completed"},
    2727: {"es": "Cancelada", "pt": "Cancelada", "en": "Cancelled"},
    2734: {"es": "Nueva preparada", "pt": "Nova preparada", "en": "New ready"},
}


def task_running_status(value: Any, language: str = "es") -> str:
    """Return a localized label for a raw code or an already formatted value."""
    text = str(value or "").strip()
    # Context formatters may pass values such as ``Em curso (2725)`` back
    # through this helper. Recover the authoritative code instead of treating
    # the localized label as an unknown status.
    code_match = re.search(r"\((\d+)\)\s*$", text)
    normalized = code_match.group(1) if code_match else text
    try:
        code = int(normalized)
    except (TypeError, ValueError):
        return ""
    labels = SYS_TASK_RUNNING_STATUS.get(code)
    if labels is None:
        return str(code)
    return f"{labels.get(language, labels['en'])} ({code})"
