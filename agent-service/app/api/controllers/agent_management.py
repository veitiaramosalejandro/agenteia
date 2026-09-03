from __future__ import annotations

import asyncio
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, status

from app.api.schemas.common import (
    AgentKnowledgeRequest,
    AgentKnowledgeResponse,
    AgentWorkRoomConfiguration,
    AgentWorkRoomConfigurationResponse,
    MultiAgentAnswer,
    MultiAgentDialogueRequest,
    MultiAgentDialogueResponse,
)
from app.agent.tools import solidset_send_chat_message
from app.connectors.db_client import (
    configure_agent_workroom,
    get_active_agents_for_workroom,
    get_agent_knowledge,
    get_solidset_instance,
    save_agent_knowledge,
    touch_agent_session,
)
from app.services.auto_reply import (
    _agent_visible_name,
    _invoke_orchestrator_for_instance,
    _learn_agent_interaction,
)
from app.system.reaction_capture import get_agent_reinforcement_context


router = APIRouter(tags=["SolidSET Agents"])
agent = None


def configure(runtime_agent: Any) -> None:
    global agent
    agent = runtime_agent


@router.post(
    "/api/v1/agent/solidset/agents/{agent_resource_id}/knowledge",
    response_model=AgentKnowledgeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_knowledge(
    agent_resource_id: uuid.UUID,
    request: AgentKnowledgeRequest,
) -> AgentKnowledgeResponse:
    """Persiste e indexa conocimiento exclusivo de un agente IA."""
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    payload["IDResource"] = agent_resource_id
    try:
        saved = await asyncio.to_thread(save_agent_knowledge, payload)
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(
            status_code=404, detail="O agente indicado não existe."
        ) from exc
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível guardar o conhecimento."
        ) from exc
    indexed = await asyncio.to_thread(
        agent.sistema_aprendizaje.aprender_conocimiento_agente, saved
    )
    return AgentKnowledgeResponse(**saved, indexed=indexed)


@router.put(
    "/api/v1/agent/solidset/agents/{agent_resource_id}/workrooms/{workroom_id}",
    response_model=AgentWorkRoomConfigurationResponse,
)
async def set_agent_workroom_configuration(
    agent_resource_id: uuid.UUID,
    workroom_id: uuid.UUID,
    request: AgentWorkRoomConfiguration,
) -> AgentWorkRoomConfigurationResponse:
    """Activa, desactiva u ordena un agente dentro de un canal."""
    try:
        saved = await asyncio.to_thread(
            configure_agent_workroom,
            agent_resource_id,
            workroom_id,
            active=request.active,
            response_order=request.response_order,
        )
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(
            status_code=404, detail="O agente indicado não existe."
        ) from exc
    return AgentWorkRoomConfigurationResponse(**saved)


@router.post(
    "/api/v1/agent/solidset/multi-agent/dialogue",
    response_model=MultiAgentDialogueResponse,
)
async def handle_multi_agent_dialogue(
    request: MultiAgentDialogueRequest,
) -> MultiAgentDialogueResponse:
    """Ejecuta de forma independiente los agentes seleccionados por SolidSET."""
    selected = list(dict.fromkeys(request.SelectedAgentResourceIds))
    if not selected:
        raise HTTPException(status_code=422, detail="Selecione, pelo menos, um agente.")
    if len(selected) > 10:
        raise HTTPException(
            status_code=422, detail="É permitido um máximo de 10 agentes por mensagem."
        )

    solidset_instance = None
    if request.SendToSolidSET:
        if not str(request.SolidSETInstanceCode or "").strip():
            raise HTTPException(
                status_code=422,
                detail="SolidSETInstanceCode é obrigatório quando SendToSolidSET=true.",
            )
        solidset_instance = get_solidset_instance(
            code=str(request.SolidSETInstanceCode).strip()
        )
        if solidset_instance is None:
            raise HTTPException(
                status_code=404,
                detail="A instância SolidSET não existe ou está inativa.",
            )

    configured_agents = get_active_agents_for_workroom(request.IDWorkRoom, selected)
    if not configured_agents:
        raise HTTPException(
            status_code=404,
            detail="Nenhum dos agentes selecionados está ativo e atribuído ao canal.",
        )

    conversation_id = request.IDSession or uuid.uuid4()

    async def execute_one(configured_agent: dict[str, Any]) -> MultiAgentAnswer:
        agent_resource_id = str(configured_agent["IDResource"])
        agent_identity_id = str(configured_agent.get("IDAgentResource") or "").strip()
        if not agent_identity_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "O recurso humano selecionado não tem um IDAgentResource "
                    "sincronizado a partir de dbo.SysResource2Agent."
                ),
            )
        agent_name = _agent_visible_name(configured_agent)
        isolated_session = (
            f"solidset:agent:{agent_resource_id}:room:{request.IDWorkRoom}:"
            f"conversation:{conversation_id}"
        )
        private_knowledge = await asyncio.to_thread(
            get_agent_knowledge,
            agent_resource_id,
            request.IDWorkRoom,
        )
        reinforcement = await asyncio.to_thread(
            get_agent_reinforcement_context,
            agent_resource_id,
            request.IDWorkRoom,
        )
        await asyncio.to_thread(
            touch_agent_session,
            conversation_id,
            agent_resource_id,
            request.IDWorkRoom,
        )
        response_text = await asyncio.to_thread(
            _invoke_orchestrator_for_instance,
            str(solidset_instance["Code"]) if solidset_instance else "",
            session_id=isolated_session,
            user_text=request.RawMessage.strip(),
            user_id=str(request.SenderResourceId or "solidset-user"),
            canal_id=str(request.IDWorkRoom),
            message_metadata={
                "agent_resource_id": agent_resource_id,
                "agent_name": agent_name,
                "agent_knowledge": private_knowledge,
                "agent_reinforcement": reinforcement,
                "workroom_id": str(request.IDWorkRoom),
                "source": "solidset_multi_agent",
                "solidset_instance_id": str(solidset_instance["ID"])
                if solidset_instance
                else "",
                "solidset_instance_code": str(solidset_instance["Code"])
                if solidset_instance
                else "",
            },
            auto_reply_mode=True,
        )
        await asyncio.to_thread(
            _learn_agent_interaction,
            agent_resource_id=agent_resource_id,
            channel_id=str(request.IDWorkRoom),
            session_id=isolated_session,
            user_text=request.RawMessage.strip(),
            response_text=response_text,
        )
        sent = False
        send_detail = None
        if request.SendToSolidSET:
            send_detail = str(
                await asyncio.to_thread(
                    solidset_send_chat_message.invoke,
                    {
                        "canal_id": str(request.IDWorkRoom),
                        "mensaje": f"{agent_name}: {response_text}",
                        "confirm": True,
                        "generated_by_ia": True,
                        "agent_resource_id": agent_resource_id,
                        "agent_identity_id": agent_identity_id or None,
                        "recurso_id": str(request.SenderResourceId or "") or None,
                        "solidset_base_url": str(solidset_instance["BaseUrl"]),
                    },
                )
            )
            sent = send_detail.startswith("✅")
        return MultiAgentAnswer(
            IDAgentResource=uuid.UUID(agent_identity_id),
            AgentName=agent_name,
            response=response_text,
            sent=sent,
            sendDetail=send_detail,
        )

    responses = await asyncio.gather(*(execute_one(item) for item in configured_agents))
    return MultiAgentDialogueResponse(
        IDSession=conversation_id,
        IDWorkRoom=request.IDWorkRoom,
        responses=list(responses),
    )
