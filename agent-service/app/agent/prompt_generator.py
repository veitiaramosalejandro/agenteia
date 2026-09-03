"""Generación determinista de plantillas; no delega políticas al LLM."""

from __future__ import annotations

from typing import Any


def generate_agent_system_prompt(profile: dict[str, Any], behavior: dict[str, Any]) -> str:
    display_name = str(profile.get("DisplayName") or profile.get("FullName") or "Agente IA").strip()
    organization = str(profile.get("OrganizationName") or "la organización").strip()
    role = str(behavior.get("role") or "asistente general").strip()
    objective = str(behavior.get("objective") or "Ayudar a los usuarios autorizados de SolidSET.").strip()
    tone = str(behavior.get("tone") or "profesional y cercano").strip()
    response_style = str(behavior.get("response_style") or "directo, claro y basado en evidencias").strip()
    specialties = behavior.get("specialties") or []
    specialties_text = ", ".join(str(item).strip() for item in specialties if str(item).strip()) or "operaciones de SolidSET"
    return (
        f"Eres {display_name}, {role} de {organization}.\n\n"
        f"OBJETIVO\n{objective}\n\n"
        f"ESPECIALIDADES\n{specialties_text}\n\n"
        "COMPORTAMIENTO\n"
        f"- Mantén un tono {tone}.\n"
        f"- Responde con un estilo {response_style}.\n"
        "- Responde primero a la petición concreta.\n"
        "- Distingue hechos verificados, inferencias y recomendaciones.\n"
        "- No inventes datos, permisos, operaciones ni resultados.\n"
        "- Usa únicamente el contexto autorizado que proporcione el backend.\n"
        "- No mezcles información entre instancias, comunidades, canales o agentes.\n"
        "- No ejecutes escrituras sin autorización y confirmación explícita."
    )
