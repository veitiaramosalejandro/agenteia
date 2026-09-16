"""Enforce clear topic exclusions from the selected agent's published policy."""

from __future__ import annotations

import re
import unicodedata

from app.connectors.db_client import get_active_agent_prompt


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value).casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


_TOPICS = (
    (r"\b(?:medic|salud|saude|health|tratamiento|treatment)\w*\b",
     r"\b(?:dolor|lumbar|medic|salud fisica|saude fisica|mental|sintoma|diagnostic|tratamiento|treatment|back pain)\w*\b"),
    (r"\b(?:legal|juridic|lawyer|abogad|advogad)\w*\b",
     r"\b(?:demanda judicial|asesoria legal|consejo legal|representacion juridica|legal advice)\b"),
    (r"\b(?:recetas de cocina|cozinha|cooking)\b",
     r"\b(?:receta de cocina|cocinar|cozinhar|cooking recipe|reposteria|pastel|tarta|cake)\b"),
    (r"\b(?:viajes|viagens|travel)\b",
     r"\b(?:viaje|viajar|viagem|travel itinerary|travel plan)\b"),
    (r"\b(?:deportes|desportos|sports)\b",
     r"\b(?:futbol|baloncesto|tenis|football|basketball|deporte|desporto)\b"),
    (r"\b(?:entretenimiento|entretenimento|entertainment)\b",
     r"\b(?:pelicula|serie de television|musica pop|movie|tv show)\b"),
    (r"\b(?:codigo de programacion|suporte tecnico|software|programming)\b",
     r"\b(?:escribe codigo|programa una aplicacion|soporte tecnico de software|write code)\b"),
    (r"\b(?:recursos humanos|human resources|despidos)\b",
     r"\b(?:redacta un despido|carta de despido|evaluacion de desempeno personal|termination letter)\b"),
)


def answer_restricted_topic(
    message: str, instance_id: str | None, resource_id: str | None,
) -> str | None:
    if not instance_id or not resource_id:
        return None
    question = _plain(message)
    candidate_topics = [restriction for restriction, topic in _TOPICS if re.search(topic, question)]
    if not candidate_topics:
        return None
    try:
        published = get_active_agent_prompt(instance_id, resource_id)
    except Exception as exc:
        print(f"AGENT_RESTRICTION_LOOKUP_FAILED type={type(exc).__name__}", flush=True)
        raise
    behavior = (published or {}).get("BehaviorConfig") or {}
    if not isinstance(behavior, dict):
        return None
    restrictions = behavior.get("restrictions") or []
    if not isinstance(restrictions, list):
        return None
    for restriction in restrictions:
        if any(re.search(topic, _plain(restriction)) for topic in candidate_topics):
            role = str(behavior.get("role") or "").strip().rstrip(".")
            if "¿" in message or re.search(r"\b(?:que|cual|puedes|debo)\b", question):
                return (f"Mi rol configurado es {role}. " if role else "") + (
                    "No puedo aconsejarte sobre ese tema fuera de mi especialidad."
                )
            if re.search(r"\b(?:what|which|can you|should i)\b", question):
                return (f"My role is {role}. " if role else "") + (
                    "I cannot advise on that topic outside my specialty."
                )
            return (f"O meu papel configurado é {role}. " if role else "") + (
                "Não posso aconselhar sobre esse tema fora da minha especialidade."
            )
    return None
