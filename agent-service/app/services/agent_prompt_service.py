from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from app.agent.prompt_generator import generate_agent_system_prompt
from app.api.schemas.agent_prompts import (
    AgentPromptBulkItem,
    AgentPromptBulkResponse,
    AgentPromptGenerateRequest,
)
from app.connectors.db_client import (
    create_agent_prompt_draft,
    get_agent_scope_profile,
    get_latest_agent_prompt,
    get_solidset_instance,
    list_active_agent_resource_ids,
    publish_agent_prompt,
)


class AgentPromptNotFound(LookupError):
    pass


class AgentPromptPersistenceError(RuntimeError):
    pass


def _instance(instance_code: str) -> dict[str, Any]:
    instance = get_solidset_instance(code=instance_code.strip(), source_ip=None)
    if not instance:
        raise AgentPromptNotFound("Instância SolidSET desconhecida.")
    return instance


def generate_prompt_draft(
    instance_code: str, resource_id: UUID, request: AgentPromptGenerateRequest,
) -> dict[str, Any]:
    instance = _instance(instance_code)
    try:
        profile = get_agent_scope_profile(instance["ID"], resource_id)
        if profile is None:
            raise AgentPromptNotFound("O agente não tem um perfil SolidSET sincronizado.")
        behavior = request.model_dump(exclude={"name", "created_by"})
        return create_agent_prompt_draft(
            instance["ID"], resource_id,
            name=request.name.strip(),
            system_prompt=generate_agent_system_prompt(profile, behavior),
            behavior_config=behavior,
            created_by=request.created_by.strip(),
        )
    except AgentPromptNotFound:
        raise
    except LookupError as exc:
        raise AgentPromptNotFound(str(exc)) from exc
    except (ValueError, psycopg.Error) as exc:
        raise AgentPromptPersistenceError("Não foi possível gerar a plantilla do agente.") from exc


def generate_active_prompt_drafts(
    instance_code: str, request: AgentPromptGenerateRequest,
) -> AgentPromptBulkResponse:
    instance = _instance(instance_code)
    behavior = request.model_dump(exclude={"name", "created_by"})
    resource_ids = list_active_agent_resource_ids(instance["ID"])
    items: list[AgentPromptBulkItem] = []
    generated = unchanged = failed = 0

    for resource_id in resource_ids:
        try:
            profile = get_agent_scope_profile(instance["ID"], resource_id)
            if profile is None:
                raise LookupError("El agente no tiene un alcance SolidSET sincronizado.")
            system_prompt = generate_agent_system_prompt(profile, behavior)
            latest = get_latest_agent_prompt(instance["ID"], resource_id)
            if (
                latest
                and str(latest.get("SystemPrompt") or "") == system_prompt
                and dict(latest.get("BehaviorConfig") or {}) == behavior
            ):
                unchanged += 1
                items.append(AgentPromptBulkItem(
                    IDResource=resource_id, result="unchanged",
                    promptID=latest["ID"], version=int(latest["Version"]),
                ))
                continue
            saved = create_agent_prompt_draft(
                instance["ID"], resource_id,
                name=request.name.strip(), system_prompt=system_prompt,
                behavior_config=behavior, created_by=request.created_by.strip(),
            )
            generated += 1
            items.append(AgentPromptBulkItem(
                IDResource=resource_id, result="generated",
                promptID=saved["ID"], version=int(saved["Version"]),
            ))
        except (ValueError, LookupError, psycopg.Error) as exc:
            failed += 1
            print(f"❌ No se pudo generar prompt para recurso {resource_id}: {type(exc).__name__}")
            items.append(AgentPromptBulkItem(
                IDResource=resource_id, result="failed",
                error="No fue posible generar la plantilla para este agente.",
            ))

    return AgentPromptBulkResponse(
        status="completed" if failed == 0 else "partial",
        activeAgents=len(resource_ids), generated=generated,
        unchanged=unchanged, failed=failed, items=items,
    )


def publish_prompt_draft(
    instance_code: str, resource_id: UUID, prompt_id: UUID,
) -> tuple[dict[str, Any], str]:
    instance = _instance(instance_code)
    try:
        saved = publish_agent_prompt(instance["ID"], resource_id, prompt_id)
        return saved, str(instance["ID"])
    except LookupError as exc:
        raise AgentPromptNotFound(str(exc)) from exc
    except (ValueError, psycopg.Error) as exc:
        raise AgentPromptPersistenceError("Não foi possível publicar a plantilla do agente.") from exc
