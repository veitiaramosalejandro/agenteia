from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import psycopg
import pymssql
from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.common import (
    SolidSETReactionCaptureRequest,
    SolidSETReactionCaptureResponse,
    UserFeedbackRequest,
    UserFeedbackResponse,
)
from app.connectors.db_client import agent_learning_enabled
from app.services.auto_reply import _agent_visible_name
from app.services.instance_resolution import _resolve_request_solidset_instance
from app.system.reaction_capture import (
    classify_reaction,
    reaction_reward,
    resolve_agent_message,
    save_agent_reaction,
)
from app.system.schema import Actividad


router = APIRouter()
agent = None


def configure(runtime_agent: Any) -> None:
    global agent
    agent = runtime_agent


@router.post("/api/v1/agent/feedback", response_model=UserFeedbackResponse)
def submit_feedback(req: UserFeedbackRequest):
    """Registra feedback explícito o correcciones del usuario para aprendizaje a largo plazo."""
    try:
        reaction = agent.sistema_aprendizaje.analyze_reaction_patterns(
            user_text=req.user_text,
            agent_response=req.agent_response,
            previous_user_text=req.previous_user_text,
        )
        learned = agent.sistema_aprendizaje.registrar_feedback_usuario(
            user_id=req.user_id,
            canal_id=req.canal_id,
            session_id=req.session_id,
            user_text=req.user_text,
            agent_response=req.agent_response,
            corrected_response=req.corrected_response,
            feedback_type=req.feedback_type,
            reason=req.reason,
            previous_user_text=req.previous_user_text,
            implicit=req.feedback_type.lower() == "implicit",
        )

        profile_updated = False
        if req.update_profile:
            profile_updated = agent.sistema_aprendizaje.actualizar_perfil_usuario(
                user_id=req.user_id,
                canal_id=req.canal_id,
                recent_user_text=req.user_text,
                recent_agent_response=req.corrected_response or req.agent_response,
                feedback_summary=req.reason or reaction.get("signal"),
            )

        return UserFeedbackResponse(
            status="ok" if learned else "warning",
            learned=learned,
            profile_updated=profile_updated,
            reaction_signal=reaction.get("signal", "sem_sinal"),
            topics=reaction.get("topics", []),
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Não foi possível registar o feedback do utilizador.",
        )


@router.post(
    "/api/v1/agent/solidset/reactions/capture",
    response_model=SolidSETReactionCaptureResponse,
    status_code=status.HTTP_201_CREATED,
)
def capture_solidset_agent_reaction(
    req: SolidSETReactionCaptureRequest,
    request: Request,
) -> SolidSETReactionCaptureResponse:
    """Captura una reacción ya registrada en SolidSET y la aprende para su agente."""
    try:
        print(req)
        instance = _resolve_request_solidset_instance(request)
        if not instance or not instance.get("DataAPI"):
            raise RuntimeError(
                "A instância SolidSET não tem uma SolidSET Data API configurada."
            )
        message = resolve_agent_message(req.IDChat, instance)
    except (pymssql.Error, psycopg.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível determinar a mensagem que recebeu a reação.",
        ) from exc
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="O chat não existe ou não foi enviado por um agente de IA registado.",
        )

    channel_id = req.IDChannel
    if channel_id.int == 0 and message.get("IDWorkRoom"):
        channel_id = uuid.UUID(str(message["IDWorkRoom"]))
    signal = classify_reaction(req.IDEmoji, req.Counter)
    reward = reaction_reward(signal, req.Counter)
    reaction_data = {
        "IDChat": req.IDChat,
        "IDUser": req.IDUser,
        "IDChannel": channel_id,
        "IDEmoji": req.IDEmoji.strip(),
        "Counter": req.Counter,
        "Signal": signal,
        "Reward": reward,
        "IDAgentResource": message["IDAgentResource"],
        "AgentResponse": str(message.get("RawMessage") or ""),
    }
    try:
        _, changed = save_agent_reaction(reaction_data)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível guardar a reação ao agente.",
        ) from exc

    learned = False
    if (
        changed
        and signal != "removed"
        and agent_learning_enabled(message["IDAgentResource"], "reactions")
    ):
        learned = bool(
            agent.sistema_aprendizaje.aprender_actividad(
                Actividad(
                    id=f"agent_reaction_{req.IDChat}_{req.IDUser}_{req.IDEmoji}",
                    recurso_humano_id=str(message["IDAgentResource"]),
                    canal_id=str(channel_id),
                    tipo=f"agent_reaction_{signal}",
                    descripcion=(
                        f"Reacción {req.IDEmoji} ({signal}) del usuario {req.IDUser} "
                        f"a la respuesta del agente: {str(message.get('RawMessage') or '')[:1000]}"
                    ),
                    timestamp=datetime.now(),
                    metadatos={
                        "source": "solidset_reaction",
                        "id_chat": req.IDChat,
                        "id_user": str(req.IDUser),
                        "id_channel": str(channel_id),
                        "id_emoji": req.IDEmoji,
                        "counter": req.Counter,
                        "signal": signal,
                        "reward": reward,
                        "agent_resource_id": str(message["IDAgentResource"]),
                    },
                )
            )
        )

    agent_name = _agent_visible_name(message)
    return SolidSETReactionCaptureResponse(
        status="captured",
        learned=learned,
        changed=changed,
        signal=signal,
        reward=reward,
        IDChat=req.IDChat,
        IDAgentResource=message["IDAgentResource"],
        AgentName=agent_name,
    )
