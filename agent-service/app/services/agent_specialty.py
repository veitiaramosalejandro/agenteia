"""Answer questions about an agent's specialty from its published configuration."""

from __future__ import annotations

import re
import unicodedata

from app.connectors.db_client import get_active_agent_prompt


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def is_agent_specialty_question(message: str) -> bool:
    text = _normalize(message)
    return bool(re.search(
        r"\b(?:que tipo de especialista|cual es tu especialidad|"
        r"en que (?:eres|estas) especializado|en que eres especialista|"
        r"que especialidad tienes|eres especialista en|"
        r"qual (?:e|eh) (?:a )?tua especialidade|em que es especialista|"
        r"em que es especializado|what is your special(?:ty|ity)|"
        r"what kind of specialist are you|what are you specialized in)\b",
        text,
    ))


def answer_agent_specialty_question(
    message: str, instance_id: str | None, resource_id: str | None,
) -> str | None:
    if not is_agent_specialty_question(message):
        return None

    language = "es" if re.search(r"\b(?:que|cual|eres|especialidad)\b", _normalize(message)) else (
        "en" if re.search(r"\b(?:what|you|specialized)\b", _normalize(message)) else "pt"
    )
    if not instance_id or not resource_id:
        return {
            "es": "No puedo confirmar una especialidad configurada para este agente.",
            "pt": "Não consigo confirmar uma especialidade configurada para este agente.",
            "en": "I cannot confirm a configured specialty for this agent.",
        }[language]

    try:
        published = get_active_agent_prompt(instance_id, resource_id)
    except Exception as exc:
        print(f"AGENT_SPECIALTY_LOOKUP_FAILED type={type(exc).__name__}", flush=True)
        return {
            "es": "No puedo confirmar ahora la especialidad configurada para este agente.",
            "pt": "Não consigo confirmar agora a especialidade configurada para este agente.",
            "en": "I cannot confirm this agent's configured specialty right now.",
        }[language]
    behavior = (published or {}).get("BehaviorConfig") or {}
    role = str(behavior.get("role") or "").strip() if isinstance(behavior, dict) else ""
    specialties = behavior.get("specialties") if isinstance(behavior, dict) else None
    names = [str(value).strip() for value in specialties if str(value).strip()] if isinstance(specialties, list) else []
    if not role and not names:
        return {
            "es": "No tengo una especialidad configurada y publicada para este agente.",
            "pt": "Não tenho uma especialidade configurada e publicada para este agente.",
            "en": "I do not have a configured and published specialty for this agent.",
        }[language]
    if role:
        response = {
            "es": f"Mi rol configurado es {role}.",
            "pt": f"O meu papel configurado é {role}.",
            "en": f"My configured role is {role}.",
        }[language]
    else:
        response = ""
    if names:
        detail = ", ".join(names[:5])
        response += (" " if response else "") + {
            "es": f"Mis especialidades son: {detail}.",
            "pt": f"As minhas especialidades são: {detail}.",
            "en": f"My specialties are: {detail}.",
        }[language]
    return response
