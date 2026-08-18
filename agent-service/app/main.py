import asyncio
import hashlib
import json
import os
import re
import socket
import ssl
import threading
import uuid
import httpx
import psycopg
import pymssql
import redis
from contextlib import suppress
from collections import OrderedDict
from datetime import datetime
from time import perf_counter, time
from typing import Any, Optional
from urllib import error as urlerror
from urllib.parse import urlparse
from urllib.request import Request as URLRequest, urlopen
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from langchain_community.chat_message_histories import RedisChatMessageHistory
from app.config import settings

from app.agent.core import MachiningAgent
from app.agent.orchestrator import SolidSETOrchestrator
from app.agent.speech import text_to_speech
from app.agent.tools import solidset_send_chat_message
from app.connectors.db_client import (
    configure_agent_workroom,
    ensure_payload_agent_workroom_assignments,
    get_active_agents_for_workroom,
    get_agent_knowledge,
    save_agent_knowledge,
    save_sys_resource_ia,
    touch_agent_session,
)
from app.system.ingest import ingestar_sistema_completo
from app.system.notification_listener import NotificationApiListener
from app.system.resource_ingest import (
    ingest_solidset_chat_resources,
    ingest_solidset_logins,
    ingest_solidset_resources,
    ingest_solidset_workrooms,
)
from app.system.reaction_capture import (
    classify_reaction,
    resolve_agent_message,
    save_agent_reaction,
)
from app.system.schema import Actividad

# ============================================================
# CONFIGURACIÓN DE LA APLICACIÓN
# ============================================================

app = FastAPI(
    title="Agent API",
    description="Agente inteligente",
    version="1.0.0"
)

# CORS para permitir conexiones desde el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instancia del agente
agent = MachiningAgent()
orchestrator = SolidSETOrchestrator(agent)
notification_listener = NotificationApiListener()

_active_dialogues = 0
_active_dialogues_lock = threading.Lock()
_dialogue_slots = threading.BoundedSemaphore(value=max(1, settings.DIALOGUE_MAX_CONCURRENT))
_dialogue_cache_lock = threading.Lock()
_dialogue_response_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_dialogue_redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

_dialogue_metrics_lock = threading.Lock()
_dialogue_metrics = {
    "count": 0,
    "total_seconds": 0.0,
    "max_seconds": 0.0,
    "last_seconds": None,
    "cache_hits": 0,
}

_auto_reply_lock = threading.Lock()
_auto_reply_seen_fingerprints: "OrderedDict[str, float]" = OrderedDict()
_auto_reply_max_seen = 2000
_auto_reply_background_tasks: set[asyncio.Task] = set()
_auto_reply_followups: dict[str, float] = {}


def _auto_reply_seen(fingerprint: str) -> bool:
    key = (fingerprint or "").strip()
    if not key:
        return False
    with _auto_reply_lock:
        return key in _auto_reply_seen_fingerprints


def _remember_auto_reply_fingerprint(fingerprint: str) -> None:
    key = (fingerprint or "").strip()
    if not key:
        return
    with _auto_reply_lock:
        _auto_reply_seen_fingerprints[key] = time()
        _auto_reply_seen_fingerprints.move_to_end(key)
        while len(_auto_reply_seen_fingerprints) > _auto_reply_max_seen:
            _auto_reply_seen_fingerprints.popitem(last=False)


def _auto_reply_followup_key(candidate: dict) -> str:
    scope = str(candidate.get("reply_resource") or candidate.get("channel_id") or "").strip().lower()
    sender = str(candidate.get("sender_resource") or candidate.get("sender_name") or "").strip().lower()
    digest = hashlib.sha256(f"{scope}|{sender}".encode("utf-8")).hexdigest()
    return f"machining:auto-reply:followup:{digest}"


def _has_active_auto_reply_followup(candidate: dict) -> bool:
    key = _auto_reply_followup_key(candidate)
    try:
        return bool(_dialogue_redis.exists(key))
    except redis.RedisError:
        with _auto_reply_lock:
            expires_at = _auto_reply_followups.get(key, 0.0)
            if expires_at <= time():
                _auto_reply_followups.pop(key, None)
                return False
            return True


def _remember_auto_reply_followup(candidate: dict) -> None:
    key = _auto_reply_followup_key(candidate)
    ttl = max(30, settings.SOLIDSET_AUTO_REPLY_FOLLOWUP_TTL_SECONDS)
    try:
        _dialogue_redis.setex(key, ttl, "1")
    except redis.RedisError:
        with _auto_reply_lock:
            _auto_reply_followups[key] = time() + ttl


def _is_self_sender(sender_resource: str, sender_name: str) -> bool:
    own_resource = (settings.SOLIDSET_RESOURCE_ID or "").strip().lower()
    sender_resource_norm = (sender_resource or "").strip().lower()
    # Sender.resource es la identidad canónica. El nombre/login puede coincidir
    # con alias visibles o venir incompleto y no debe descartar usuarios reales.
    return bool(own_resource and sender_resource_norm and own_resource == sender_resource_norm)


def _sanitize_auto_reply_input(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""
    mention_tokens = {
        (settings.SOLIDSET_AUTO_REPLY_MENTION_TOKEN or "").strip(),
        "@agente", "@asistente", "@agent", "@assistant",
    }
    for mention_token in (token for token in mention_tokens if token):
        text = re.sub(re.escape(mention_token), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:agente|asistente|agent|assistant)\s*[,;:\-]?\s*", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    max_len = max(80, settings.SOLIDSET_AUTO_REPLY_MAX_INPUT_CHARS)
    return text[:max_len].strip()


def _looks_like_question_or_request(raw_text: str) -> bool:
    text = " ".join((raw_text or "").strip().lower().split())
    if not text:
        return False
    if "?" in text or "¿" in text:
        return True
    starters = (
        "qué ", "que ", "cómo ", "como ", "cuál ", "cual ", "cuándo ", "cuando ",
        "dónde ", "donde ", "quién ", "quien ", "por qué ", "puedes ", "podrías ",
        "dime ", "busca ", "consulta ", "explica ", "ayúdame ", "ayudame ",
        "what ", "how ", "when ", "where ", "who ", "why ", "can you ", "please ",
        "o que ", "como ", "quando ", "onde ", "quem ", "por que ", "pode ", "procura ",
    )
    return text.startswith(starters)


def _message_mentions_agent(raw_text: str) -> bool:
    text = (raw_text or "").strip()
    if not text:
        return False
    configured = (settings.SOLIDSET_AUTO_REPLY_MENTION_TOKEN or "").strip()
    if configured and configured.lower() in text.lower():
        return True
    if re.search(r"(?<!\w)@(?:agente|asistente|agent|assistant)(?!\w)", text, flags=re.IGNORECASE):
        return True
    return bool(
        re.match(
            r"^\s*(?:agente|asistente|agent|assistant)(?:\s*[,;:\-]\s*|\s+)(?=\S)",
            text,
            flags=re.IGNORECASE,
        )
    )


def _schedule_auto_replies(candidates: list[dict]) -> None:
    """Mantiene una referencia fuerte y registra fallos de la tarea en background."""
    if not candidates:
        return
    task = asyncio.create_task(_process_auto_replies(candidates))
    _auto_reply_background_tasks.add(task)

    def _completed(done: asyncio.Task) -> None:
        _auto_reply_background_tasks.discard(done)
        try:
            sent = done.result()
            print(f"🤖 Procesamiento de auto-respuesta finalizado; enviadas={sent}")
        except asyncio.CancelledError:
            print("⚠️ Procesamiento de auto-respuesta cancelado")
        except Exception as exc:
            print(f"❌ Error no controlado procesando auto-respuesta: {exc}")

    task.add_done_callback(_completed)


def _is_safe_auto_reply_output(response_text: str) -> bool:
    """Impide publicar en SolidSET trazas, errores o resultados crudos de herramientas."""
    text = (response_text or "").strip().lower()
    if not text:
        return False
    forbidden = (
        "basado en la información obtenida: status=",
        "se alcanzó el límite de iteraciones",
        "validation error",
        "error al ejecutar la herramienta",
        "status=200",
        "method=get",
        "method=post",
        "body={",
        "body=[",
        "http://localhost",
        "https://localhost",
        "traceback (most recent call last)",
        "pydantic.dev",
    )
    return not any(marker in text for marker in forbidden)


def _weather_location_prompt(raw_text: str) -> Optional[str]:
    """Devuelve una aclaración si se pide el tiempo sin indicar ubicación."""
    text = " ".join((raw_text or "").strip().lower().split())
    weather_terms = ("tiempo", "tempo", "clima", "weather", "forecast", "meteorologia", "previsão", "previsao")
    if not any(term in text for term in weather_terms):
        return None
    has_location = bool(re.search(r"\b(?:en|para|in|at|for|em)\s+[\wáéíóúüñãõç-]{2,}", text, flags=re.IGNORECASE))
    if has_location:
        return None
    if any(term in text for term in ("weather", "forecast", "today")):
        return "Which city or location would you like the weather forecast for?"
    if any(term in text for term in ("previsão", "previsao", "meteorologia", "hoje")):
        return "Para qual cidade ou localidade você quer consultar o tempo?"
    return "¿De qué ciudad o localidad quieres conocer el tiempo?"


def _direct_courtesy_response(raw_text: str) -> Optional[str]:
    """Responde cumplidos/agradecimientos directos sin ocupar SQL, RAG u Ollama."""
    text = " ".join((raw_text or "").strip().lower().split())
    if not text or _looks_like_question_or_request(text):
        return None
    if any(term in text for term in ("obrigado", "obrigada", "boa explicação", "boa explicacao", "muito bom")):
        return "Muito obrigado. Fico satisfeito por a explicação ter sido útil."
    if any(term in text for term in ("thank you", "thanks", "good explanation", "great explanation", "well explained")):
        return "Thank you. I am glad the explanation was helpful."
    if any(term in text for term in ("gracias", "buena explicación", "buena explicacion", "muy buena", "bien explicado")):
        return "Muchas gracias. Me alegra que la explicación haya sido útil."
    return None


def _is_external_information_query(raw_text: str) -> bool:
    """Separa consultas externas actuales de conocimiento operativo de trabajo."""
    text = " ".join((raw_text or "").strip().lower().split())
    external_terms = (
        "tiempo", "tempo", "clima", "pronostico", "pronóstico", "meteorologia", "meteorología",
        "weather", "forecast", "previsão", "previsao", "noticias", "news",
        "resultado deportivo", "precio actual", "cotizacion", "cotización",
    )
    return any(term in text for term in external_terms)


def _auto_reply_rejection_reason(candidate: dict) -> Optional[str]:
    fingerprint = (candidate.get("fingerprint") or "").strip()
    if not fingerprint:
        return "sin_fingerprint"
    if _auto_reply_seen(fingerprint):
        return "fingerprint_ya_respondido"
    if candidate.get("generated_by_ia"):
        return "mensaje_generado_por_ia"

    channel_id = (candidate.get("channel_id") or "").strip()
    message = (candidate.get("message") or "").strip()
    sender_resource = str(candidate.get("sender_resource") or "")
    sender_name = str(candidate.get("sender_name") or "")
    can_reply_direct = bool(candidate.get("is_direct") and candidate.get("reply_resource"))
    if not message:
        return "mensaje_vacio"
    if not channel_id and not can_reply_direct:
        return "sin_destino_para_responder"

    # Con identidad explícita configurada, Destiny es la fuente de verdad. Así
    # una mención textual dentro de un canal ajeno no provoca una respuesta.
    has_configured_recipient_identity = bool(
        (settings.SOLIDSET_LOGIN_RESOURCE_ID or "").strip()
        or (settings.SOLIDSET_RESOURCE_ID or "").strip()
    )
    if (
        has_configured_recipient_identity
        and not candidate.get("agent_resource_id")
        and not candidate.get("addressed_to_agent")
    ):
        return "destino_no_incluye_agente"
    mentioned = _message_mentions_agent(message)
    active_followup = _has_active_auto_reply_followup(candidate)
    kind_is_conversational = bool(candidate.get("kind_reply_eligible", True))
    if (
        not kind_is_conversational
        and not _looks_like_question_or_request(message)
        and not mentioned
        and not active_followup
    ):
        return f"evento_sin_peticion:{candidate.get('message_kind') or 'desconocido'}"
    if (
        not candidate.get("addressed_to_agent")
        and not mentioned
        and not _looks_like_question_or_request(message)
        and not active_followup
    ):
        return "no_es_pregunta_peticion_o_continuacion"
    if (
        not candidate.get("agent_resource_id")
        and (not settings.SOLIDSET_AUTO_REPLY_ALLOW_SELF)
        and _is_self_sender(sender_resource=sender_resource, sender_name=sender_name)
    ):
        return "remitente_es_recurso_del_agente"

    if settings.SOLIDSET_AUTO_REPLY_REQUIRE_MENTION:
        if (
            not candidate.get("addressed_to_agent")
            and not mentioned
            and not active_followup
        ):
            return "mencion_o_destino_directo_requerido"

    return None


def _selected_agent_resource_ids(candidate: dict) -> list[str]:
    """Selecciona únicamente los recursos destinatarios del mensaje dirigido."""
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    if candidate.get("meeting_id"):
        chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
        chat_lower = {str(key).lower(): value for key, value in chat.items()}
        chat_destinations = chat_lower.get("destiny")
        if isinstance(chat_destinations, list) and chat_destinations:
            responders: list[tuple[int, str]] = []
            for destination in chat_destinations:
                if not isinstance(destination, dict):
                    continue
                lowered = {
                    str(key).lower(): value for key, value in destination.items()
                }
                try:
                    destination_type = int(lowered.get("type"))
                except (TypeError, ValueError):
                    continue
                if destination_type != 2:
                    continue
                resource = str(
                    lowered.get("idresource") or lowered.get("resource") or ""
                ).strip()
                if not resource or resource == str(uuid.UUID(int=0)):
                    continue
                try:
                    sequence = int(lowered.get("sequence") or 0)
                except (TypeError, ValueError):
                    sequence = 0
                responders.append((sequence, resource))
            responders.sort(key=lambda item: item[0])
            # La presencia de Chat.destiny es autoritativa en meetings, incluso
            # si solo contiene type=1 y por tanto no hay agente destinatario.
            return list(dict.fromkeys(resource for _, resource in responders))

    destination_resources: list[str] = []
    destiny = payload.get("FrameworkDestiny")
    if isinstance(destiny, dict):
        destinations = destiny.get("Dests", destiny.get("dests", []))
        if isinstance(destinations, list):
            for destination in destinations:
                if isinstance(destination, dict):
                    lowered = {str(key).lower(): value for key, value in destination.items()}
                    value = lowered.get("resource") or lowered.get("idresource")
                    if value:
                        destination_resources.append(str(value).strip())

    destination_resources = list(dict.fromkeys(
        value for value in destination_resources
        if value and value != str(uuid.UUID(int=0))
    ))
    has_explicit_destinations = bool(destination_resources)
    # En meetings SolidSET genera una copia técnica para cada participante. La
    # copia entregada al autor puede incluir su propio recurso en Destiny.dests;
    # nunca debe activar el agente de la persona que formuló la pregunta.
    if candidate.get("meeting_id"):
        sender_resource = str(candidate.get("sender_resource") or "").strip().lower()
        if not sender_resource:
            framework_sender = payload.get("FrameworkSender")
            if isinstance(framework_sender, dict):
                sender_lower = {
                    str(key).lower(): value for key, value in framework_sender.items()
                }
                sender_resource = str(
                    sender_lower.get("resource") or sender_lower.get("idresource") or ""
                ).strip().lower()
        if sender_resource:
            destination_resources = [
                value for value in destination_resources
                if value.lower() != sender_resource
            ]
    if has_explicit_destinations:
        return destination_resources

    selected: list[str] = []
    for key in ("SelectedAgentResourceIds", "selectedAgentResourceIds", "AgentResourceIds"):
        values = payload.get(key)
        if isinstance(values, list):
            selected.extend(str(value).strip() for value in values if value)
    destiny_resource = str(candidate.get("destiny_resource") or "").strip()
    if destiny_resource and destiny_resource != str(uuid.UUID(int=0)):
        selected.append(destiny_resource)
    return list(dict.fromkeys(value for value in selected if value))


def _agent_visible_name(configured_agent: dict[str, Any]) -> str:
    """Construye la identidad pública del agente desde el nombre de su login."""
    resource_id = str(configured_agent.get("IDResource") or "").strip()
    full_name = str(configured_agent.get("FullName") or "").strip()
    fallback = str(configured_agent.get("Name") or resource_id).strip()
    identity = full_name or fallback or resource_id
    return f"Asistente IA {identity}".strip()


def _payload_participant_resource_ids(candidate: dict) -> list[str]:
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_lower = {str(key).lower(): value for key, value in chat.items()}
    participants: list[str] = []
    for collection_name in ("resourcetable", "destiny"):
        resources = chat_lower.get(collection_name)
        if not isinstance(resources, list):
            continue
        for resource in resources:
            if isinstance(resource, dict):
                lowered = {str(key).lower(): value for key, value in resource.items()}
                value = lowered.get("idresource") or lowered.get("resource")
                if value:
                    participants.append(str(value).strip())
    return list(dict.fromkeys(value for value in participants if value))


def _candidate_session_id(candidate: dict) -> uuid.UUID:
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    sender = payload.get("FrameworkSender") if isinstance(payload.get("FrameworkSender"), dict) else {}
    candidates = (
        payload.get("IDSession"),
        sender.get("Session"), sender.get("session"), sender.get("IDSession"),
        candidate.get("chat_id"),
    )
    for value in candidates:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            continue
    stable_scope = (
        f"{candidate.get('channel_id')}|{candidate.get('sender_resource')}|"
        f"{candidate.get('reply_resource')}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, f"solidset-agent-session:{stable_scope}")


def _route_candidates_to_selected_agents(candidates: list[dict]) -> list[dict]:
    """Genera una ejecución por agente activo, seleccionado y asignado al canal."""
    routed: list[dict] = []
    for candidate in candidates:
        if candidate.get("generated_by_ia"):
            continue
        channel_id = str(candidate.get("channel_id") or "").strip()
        selected = _selected_agent_resource_ids(candidate)
        if not channel_id or not selected:
            continue
        try:
            ensure_payload_agent_workroom_assignments(channel_id, selected)
            configured_agents = get_active_agents_for_workroom(channel_id, selected)
        except (ValueError, psycopg.Error) as exc:
            print(f"⚠️ No se pudo resolver agentes seleccionados para {channel_id}: {exc}")
            continue
        for configured_agent in configured_agents:
            agent_resource_id = str(configured_agent["IDResource"])
            routed_candidate = dict(candidate)
            try:
                private_knowledge = get_agent_knowledge(agent_resource_id, channel_id)
            except (ValueError, psycopg.Error) as exc:
                print(f"⚠️ Conocimiento privado no disponible para {agent_resource_id}: {exc}")
                private_knowledge = ""
            routed_candidate.update({
                "fingerprint": f"{candidate.get('fingerprint')}:{agent_resource_id}",
                "agent_resource_id": agent_resource_id,
                "agent_name": _agent_visible_name(configured_agent),
                "agent_session_id": str(_candidate_session_id(candidate)),
                "agent_knowledge": private_knowledge,
                "addressed_to_agent": True,
            })
            routed.append(routed_candidate)
    return routed


def _learn_agent_interaction(
    *,
    agent_resource_id: str,
    channel_id: str,
    session_id: str,
    user_text: str,
    response_text: str,
) -> None:
    """Guarda aprendizaje etiquetado; nunca queda visible para otro agente."""
    digest = hashlib.sha256(
        f"{agent_resource_id}|{channel_id}|{session_id}|{user_text}|{response_text}".encode("utf-8")
    ).hexdigest()[:32]
    agent.sistema_aprendizaje.aprender_actividad(Actividad(
        id=f"agent_turn_{digest}",
        recurso_humano_id=agent_resource_id,
        canal_id=channel_id,
        tipo="agent_interaction",
        descripcion=f"Consulta: {user_text}\nRespuesta: {response_text}",
        timestamp=datetime.now(),
        metadatos={
            "agent_resource_id": agent_resource_id,
            "session_id": session_id,
            "source": "solidset_multi_agent",
        },
    ))


def _candidate_qualifies_for_auto_reply(candidate: dict) -> bool:
    return _auto_reply_rejection_reason(candidate) is None


async def _process_auto_replies(candidates: list[dict]) -> int:
    if not settings.SOLIDSET_AUTO_REPLY_ENABLED:
        return 0
    if not settings.SOLIDSET_USER_ACTIONS_ENABLED:
        print("⚠️ Auto-reply SOLIDSET activo en config, pero SOLIDSET_USER_ACTIONS_ENABLED=false. No se enviarán respuestas.")
        return 0

    candidates = _route_candidates_to_selected_agents(candidates)
    # Una selección explícita de SolidSET prevalece sobre el límite histórico
    # de una sola autorrespuesta, manteniendo un techo defensivo por mensaje.
    max_replies = min(
        10,
        max(1, settings.SOLIDSET_AUTO_REPLY_MAX_PER_CYCLE, len(candidates)),
    )
    sent = 0
    local_seen = set()

    for candidate in candidates:
        if sent >= max_replies:
            break
        fingerprint = (candidate.get("fingerprint") or "").strip()
        if not fingerprint or fingerprint in local_seen:
            continue
        local_seen.add(fingerprint)

        rejection_reason = _auto_reply_rejection_reason(candidate)
        if rejection_reason is not None:
            print(
                "ℹ️ Candidato de auto-respuesta descartado por filtros "
                f"reason={rejection_reason} "
                f"addressed={bool(candidate.get('addressed_to_agent'))} "
                f"direct={bool(candidate.get('is_direct'))} "
                f"sender_resource={candidate.get('sender_resource', '-')} "
                f"sender={candidate.get('sender_name', 'desconocido')} "
                f"message={str(candidate.get('message') or '')[:120]!r}"
            )
            continue

        incoming_text = _sanitize_auto_reply_input(str(candidate.get("message") or ""))
        channel_id = (candidate.get("channel_id") or "").strip()
        is_direct = bool(candidate.get("is_direct"))
        reply_resource = str(candidate.get("reply_resource") or "").strip()
        reply_login = str(candidate.get("sender_login") or "").strip()
        visibility_level = int(candidate.get("visibility_level", 1))
        meeting_id = str(candidate.get("meeting_id") or "").strip()
        meeting_code = str(candidate.get("meeting_code") or "").strip()
        message_kind = str(candidate.get("message_kind") or "ChatMessage")
        message_category = str(candidate.get("message_category") or "chat")
        importance = int(candidate.get("importance", 0))
        message_metadata = {
            "chat_id": candidate.get("chat_id"),
            "recipient_count": int(candidate.get("recipient_count", 0)),
            "importance": importance,
            "agent_resource_id": candidate.get("agent_resource_id"),
            "agent_name": candidate.get("agent_name"),
            "agent_knowledge": candidate.get("agent_knowledge"),
            "workroom_id": channel_id,
        }
        if not incoming_text or (not channel_id and not reply_resource):
            continue

        conversation_scope = reply_resource if is_direct else channel_id
        agent_resource_id = str(candidate.get("agent_resource_id") or "").strip()
        agent_name = str(candidate.get("agent_name") or agent_resource_id).strip()
        conversation_id = str(
            candidate.get("chat_id")
            or candidate.get("agent_session_id")
            or conversation_scope
        )
        session_id = (
            f"solidset:agent:{agent_resource_id}:room:{channel_id}:"
            f"conversation:{conversation_id}"
        )
        await asyncio.to_thread(
            touch_agent_session,
            candidate.get("agent_session_id"),
            agent_resource_id,
            channel_id,
        )
        user_id = str(
            candidate.get("sender_resource")
            or candidate.get("sender_name")
            or settings.SOLIDSET_LOGIN_USERNAME
            or "solidset.agent"
        ).strip()

        response_text = _direct_courtesy_response(incoming_text) if is_direct else None
        if response_text is None:
            response_text = _weather_location_prompt(incoming_text)
        if response_text is None:
            try:
                external_query = _is_external_information_query(incoming_text)
                allowed_tools = (
                    {"google_web_search"}
                    if external_query
                    else {"query_sql_server", "get_db_schema"}
                )
                print(
                    f"🤖 Generando auto-respuesta con LLM channel={channel_id} "
                    f"target={'direct:' + reply_resource if is_direct else 'channel:' + channel_id} "
                    f"ollama={settings.OLLAMA_BASE_URL} route="
                    f"{'external_web' if external_query else 'work_sql_rag'}"
                )
                response_text = await asyncio.to_thread(
                    orchestrator.invoke,
                    session_id=session_id,
                    user_text=incoming_text,
                    user_id=user_id,
                    # Aunque la respuesta sea dirigida, el workRoom sigue siendo
                    # el contexto funcional donde nació la conversación.
                    canal_id=channel_id,
                    meeting_id=meeting_id or None,
                    meeting_code=meeting_code or None,
                    message_kind=message_kind,
                    message_category=message_category,
                    message_metadata=message_metadata,
                    tool_allowlist=allowed_tools,
                    auto_reply_mode=True,
                )
            except Exception as exc:
                print(f"⚠️ Error generando auto-respuesta para canal {channel_id}: {exc}")
                continue

        response_text = (response_text or "").strip()
        if not _is_safe_auto_reply_output(response_text):
            response_text = (
                "No pude procesar correctamente tu mensaje en este momento. "
                "Por favor, inténtalo de nuevo en unos instantes."
            )

        await asyncio.to_thread(
            _learn_agent_interaction,
            agent_resource_id=agent_resource_id,
            channel_id=channel_id,
            session_id=session_id,
            user_text=incoming_text,
            response_text=response_text,
        )

        try:
            send_result = await asyncio.to_thread(
                solidset_send_chat_message.invoke,
                {
                    "canal_id": channel_id,
                    "mensaje": f"{agent_name}: {response_text}",
                    "confirm": True,
                    "recurso_id": reply_resource if is_direct else None,
                    "recurso_login_id": reply_login if is_direct else None,
                    "visibility_level": visibility_level,
                    "kind": 7,
                    "importance": importance,
                    "meeting_id": meeting_id or None,
                    "meeting_code": meeting_code or None,
                    "meeting_mirror_general": bool(candidate.get("meeting_active")),
                    "generated_by_ia": True,
                    "agent_resource_id": agent_resource_id,
                },
            )
            send_result_text = str(send_result)
            if send_result_text.startswith("✅"):
                sent += 1
                _remember_auto_reply_fingerprint(fingerprint)
                _remember_auto_reply_followup(candidate)
                print(
                    f"🤖 Auto-reply enviado channel={channel_id} "
                    f"visibility={visibility_level} "
                    f"importance={importance} "
                    f"meeting={meeting_code or '-'} "
                    f"source_kind={message_kind} reply_kind=ChatMessage(7) "
                    f"sender={candidate.get('sender_name', 'desconocido')}"
                )
            else:
                print(f"⚠️ Auto-reply no enviado en canal {channel_id}: {send_result_text}")
        except Exception as exc:
            print(f"⚠️ Error enviando auto-respuesta a SOLIDSET (canal {channel_id}): {exc}")

    return sent


def _extract_host_port_from_url(raw_url: str, default_port: int) -> tuple[Optional[str], int]:
    parsed = urlparse(raw_url or "")
    host = parsed.hostname
    port = parsed.port or default_port
    return host, int(port)


def _extract_host_port(raw_host: str, default_port: int) -> tuple[Optional[str], int]:
    host = (raw_host or "").strip()
    if not host:
        return None, int(default_port)
    if ":" in host and not host.startswith("["):
        left, right = host.rsplit(":", 1)
        if right.isdigit():
            return left.strip(), int(right)
    return host, int(default_port)


def _probe_tcp(host: Optional[str], port: int, timeout_seconds: float = 2.5) -> dict:
    if not host:
        return {"ok": False, "error": "host_vacio", "host": host, "port": port}

    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {"ok": True, "host": host, "port": port}
    except Exception as exc:
        return {"ok": False, "host": host, "port": port, "error": str(exc)}


def _probe_http(base_url: str, path: str = "", timeout_seconds: float = 3.5, verify_tls: bool = True) -> dict:
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "url_vacia", "url": base}

    target = f"{base}{path}" if path else base
    try:
        request = URLRequest(target)
        if target.lower().startswith("https://") and not verify_tls:
            context = ssl._create_unverified_context()
            with urlopen(request, timeout=timeout_seconds, context=context) as response:
                status_code = int(getattr(response, "status", 200))
        else:
            with urlopen(request, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200))

        return {
            "ok": 200 <= status_code < 500,
            "status_code": status_code,
            "url": target,
        }
    except urlerror.HTTPError as exc:
        status_code = int(getattr(exc, "code", 0))
        return {
            "ok": 200 <= status_code < 500,
            "status_code": status_code,
            "url": target,
            "error": str(exc),
        }
    except Exception as exc:
        return {"ok": False, "url": target, "error": str(exc)}


def _probe_http_json(base_url: str, path: str, timeout_seconds: float = 4.0, verify_tls: bool = True) -> dict:
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "url_vacia", "url": base}

    target = f"{base}{path}" if path else base
    try:
        request = URLRequest(target)
        if target.lower().startswith("https://") and not verify_tls:
            context = ssl._create_unverified_context()
            response = urlopen(request, timeout=timeout_seconds, context=context)
        else:
            response = urlopen(request, timeout=timeout_seconds)

        with response as resp:
            status_code = int(getattr(resp, "status", 200))
            body = resp.read().decode("utf-8", errors="ignore")

        data = json.loads(body)
        result = {
            "ok": 200 <= status_code < 500,
            "status_code": status_code,
            "url": target,
            "json": {
                "title": data.get("info", {}).get("title") if isinstance(data, dict) else None,
                "version": data.get("info", {}).get("version") if isinstance(data, dict) else None,
                "paths_count": len(data.get("paths", {})) if isinstance(data, dict) and isinstance(data.get("paths"), dict) else None,
            },
        }
        return result
    except Exception as exc:
        return {"ok": False, "url": target, "error": str(exc)}


def _run_startup_connectivity_checks() -> dict:
    checks = {}

    ollama_host, ollama_port = _extract_host_port_from_url(settings.OLLAMA_BASE_URL, 11434)
    checks["ollama"] = {
        "tcp": _probe_tcp(ollama_host, ollama_port),
        "http": _probe_http(settings.OLLAMA_BASE_URL, "/api/tags"),
    }

    qdrant_host, qdrant_port = _extract_host_port_from_url(settings.VECTOR_DB_URL, 6333)
    checks["qdrant"] = {
        "tcp": _probe_tcp(qdrant_host, qdrant_port),
        "http": _probe_http(settings.VECTOR_DB_URL, "/collections"),
    }

    redis_host, redis_port = _extract_host_port_from_url(settings.REDIS_URL, 6379)
    checks["redis"] = {
        "tcp": _probe_tcp(redis_host, redis_port),
    }

    notif_enabled = settings.NOTIF_API_ENABLED and bool((settings.NOTIF_API_BASE_URL or "").strip())
    checks["notification_api"] = {
        "enabled": notif_enabled,
        "tcp": _probe_tcp(*_extract_host_port_from_url(settings.NOTIF_API_BASE_URL, 443)) if notif_enabled else {"ok": True, "skipped": True},
        "http": _probe_http(
            settings.NOTIF_API_BASE_URL,
            "/api/Request",
            verify_tls=settings.NOTIF_API_VERIFY_TLS,
        ) if notif_enabled else {"ok": True, "skipped": True},
    }

    solidset_rest_enabled = bool((settings.SOLIDSET_RESTAPI_BASE_URL or "").strip())
    checks["solidset_restapi"] = {
        "configured": solidset_rest_enabled,
        "tcp": _probe_tcp(*_extract_host_port_from_url(settings.SOLIDSET_RESTAPI_BASE_URL, 80)) if solidset_rest_enabled else {"ok": False, "error": "SOLIDSET_RESTAPI_BASE_URL_no_configurada"},
        "root": _probe_http(settings.SOLIDSET_RESTAPI_BASE_URL) if solidset_rest_enabled else {"ok": False, "error": "SOLIDSET_RESTAPI_BASE_URL_no_configurada"},
        "heartbeat": _probe_http(settings.SOLIDSET_RESTAPI_BASE_URL, "/RestApi/Heartbeat") if solidset_rest_enabled else {"ok": False, "error": "SOLIDSET_RESTAPI_BASE_URL_no_configurada"},
        "swagger": _probe_http(settings.SOLIDSET_RESTAPI_BASE_URL, "/swagger/index.html") if solidset_rest_enabled else {"ok": False, "error": "SOLIDSET_RESTAPI_BASE_URL_no_configurada"},        
    }

    solidset_chat_base = (settings.SOLIDSET_CHAT_BASE_URL or settings.NOTIF_API_BASE_URL or "").strip()
    solidset_chat_enabled = bool(solidset_chat_base)
    checks["solidset_chatapi"] = {
        "configured": solidset_chat_enabled,
        "tcp": _probe_tcp(*_extract_host_port_from_url(solidset_chat_base, 80)) if solidset_chat_enabled else {"ok": False, "error": "SOLIDSET_CHAT_BASE_URL_no_configurada"},
        "root": _probe_http(solidset_chat_base) if solidset_chat_enabled else {"ok": False, "error": "SOLIDSET_CHAT_BASE_URL_no_configurada"},
        "notifications": _probe_http(solidset_chat_base, "/Chat/GetNotifications") if solidset_chat_enabled else {"ok": False, "error": "SOLIDSET_CHAT_BASE_URL_no_configurada"},
    }

    sql_host, sql_port = _extract_host_port(settings.SQL_SERVER_HOST, 1433)
    checks["sql_server"] = {
        "tcp": _probe_tcp(sql_host, sql_port),
        "database": settings.SQL_SERVER_DB,
    }

    db_url = os.getenv("DB_URL", "")
    pg_host, pg_port = _extract_host_port_from_url(db_url, 5432)
    checks["postgres_timescaledb"] = {
        "configured": bool(db_url),
        "tcp": _probe_tcp(pg_host, pg_port) if db_url else {"ok": False, "error": "DB_URL_no_configurada"},
    }

    all_ok = True
    for service_data in checks.values():
        for probe_name, probe_result in service_data.items():
            if probe_name in {"enabled", "configured", "database"}:
                continue
            if isinstance(probe_result, dict) and not probe_result.get("ok", False):
                all_ok = False

    return {
        "checked_at": datetime.utcnow().isoformat(),
        "all_ok": all_ok,
        "checks": checks,
    }


def _probe_to_text(probe: dict) -> str:
    if probe.get("skipped"):
        return "SKIPPED"
    if probe.get("ok"):
        if "status_code" in probe:
            return f"OK (HTTP {probe.get('status_code')})"
        host = probe.get("host")
        port = probe.get("port")
        if host and port:
            return f"OK ({host}:{port})"
        return "OK"
    error_text = probe.get("error", "error_desconocido")
    return f"FAIL ({error_text})"


def _log_startup_connectivity(report: dict) -> None:
    checks = report.get("checks", {})
    checked_at = report.get("checked_at")
    print("🔌 Comprobador de conectividad inicial")
    if checked_at:
        print(f"   - Timestamp UTC: {checked_at}")

    ollama = checks.get("ollama", {})
    print(f"   - Ollama URL: {settings.OLLAMA_BASE_URL}")
    print(f"     • TCP: {_probe_to_text(ollama.get('tcp', {}))}")
    print(f"     • HTTP /api/tags: {_probe_to_text(ollama.get('http', {}))}")

    qdrant = checks.get("qdrant", {})
    print(f"   - Qdrant URL: {settings.VECTOR_DB_URL}")
    print(f"     • TCP: {_probe_to_text(qdrant.get('tcp', {}))}")
    print(f"     • HTTP /collections: {_probe_to_text(qdrant.get('http', {}))}")

    redis = checks.get("redis", {})
    print(f"   - Redis URL: {settings.REDIS_URL}")
    print(f"     • TCP: {_probe_to_text(redis.get('tcp', {}))}")

    sql_server = checks.get("sql_server", {})
    sql_tcp = sql_server.get("tcp", {})
    print(f"   - SQL Server: {settings.SQL_SERVER_HOST} | DB: {settings.SQL_SERVER_DB}")
    print(f"     • TCP: {_probe_to_text(sql_tcp)}")

    postgres = checks.get("postgres_timescaledb", {})
    db_url = os.getenv("DB_URL", "")
    print(f"   - PostgreSQL/TimescaleDB URL: {db_url or 'DB_URL_no_configurada'}")
    print(f"     • TCP: {_probe_to_text(postgres.get('tcp', {}))}")

    notif = checks.get("notification_api", {})
    notif_url = settings.NOTIF_API_BASE_URL or "NOTIF_API_BASE_URL_no_configurada"
    print(f"   - Notification API URL: {notif_url}")
    print(f"     • Enabled: {notif.get('enabled', False)}")
    print(f"     • TCP: {_probe_to_text(notif.get('tcp', {}))}")
    print(f"     • HTTP /api/Request: {_probe_to_text(notif.get('http', {}))}")

    solidset_rest = checks.get("solidset_restapi", {})
    solidset_rest_url = settings.SOLIDSET_RESTAPI_BASE_URL or "SOLIDSET_RESTAPI_BASE_URL_no_configurada"
    print(f"   - SolidSET RestApi URL: {solidset_rest_url}")
    print(f"     • Configured: {solidset_rest.get('configured', False)}")
    print(f"     • TCP: {_probe_to_text(solidset_rest.get('tcp', {}))}")
    print(f"     • HTTP /: {_probe_to_text(solidset_rest.get('root', {}))}")
    print(f"     • HTTP /RestApi/Heartbeat: {_probe_to_text(solidset_rest.get('heartbeat', {}))}")        


def _build_dialogue_cache_key(session_id: str, user_id: str, canal_id: Optional[str], message: str) -> str:
    normalized_message = " ".join((message or "").strip().lower().split())
    normalized_canal = (canal_id or "").strip().lower()
    normalized_user = (user_id or "").strip().lower()
    normalized_session = (session_id or "").strip().lower()
    return f"{normalized_session}|{normalized_user}|{normalized_canal}|{normalized_message}"


def _resolve_effective_canal_id(canal_id: Optional[str], session_id: str) -> Optional[str]:
    """Permite usar session_id como canal cuando integraciones no envian canal_id explícito."""
    explicit = (canal_id or "").strip()
    if explicit:
        return explicit

    session_candidate = (session_id or "").strip()
    if not session_candidate:
        return None

    # Evita tomar como canal los session_id generados por UI local (session_xxxx).
    if session_candidate.lower().startswith("session_"):
        return None

    return session_candidate


def _get_cached_dialogue_response(cache_key: str) -> Optional[str]:
    if not settings.DIALOGUE_DUPLICATE_CACHE_ENABLED:
        return None

    redis_key = (
        f"{settings.DIALOGUE_REDIS_CACHE_PREFIX}:"
        f"{hashlib.sha256(cache_key.encode('utf-8')).hexdigest()}"
    )
    try:
        cached = _dialogue_redis.get(redis_key)
        if cached:
            return cached
    except redis.RedisError as exc:
        # Redis no debe impedir responder; queda una cache local de contingencia.
        print(f"⚠️ Cache Redis no disponible; usando cache local: {exc}")

    now = time()
    ttl = max(1, settings.DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS)
    with _dialogue_cache_lock:
        item = _dialogue_response_cache.get(cache_key)
        if not item:
            return None

        timestamp, response_text = item
        if (now - timestamp) > ttl:
            _dialogue_response_cache.pop(cache_key, None)
            return None

        _dialogue_response_cache.move_to_end(cache_key)
        return response_text


def _store_cached_dialogue_response(cache_key: str, response_text: str) -> None:
    if not settings.DIALOGUE_DUPLICATE_CACHE_ENABLED or not response_text:
        return

    redis_key = (
        f"{settings.DIALOGUE_REDIS_CACHE_PREFIX}:"
        f"{hashlib.sha256(cache_key.encode('utf-8')).hexdigest()}"
    )
    ttl = max(1, settings.DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS)
    try:
        _dialogue_redis.setex(redis_key, ttl, response_text)
        return
    except redis.RedisError as exc:
        print(f"⚠️ No se pudo escribir cache Redis; usando cache local: {exc}")

    now = time()
    max_items = max(50, settings.DIALOGUE_DUPLICATE_CACHE_MAX_ITEMS)
    with _dialogue_cache_lock:
        _dialogue_response_cache[cache_key] = (now, response_text)
        _dialogue_response_cache.move_to_end(cache_key)
        while len(_dialogue_response_cache) > max_items:
            _dialogue_response_cache.popitem(last=False)


def _record_dialogue_metrics(duration_seconds: float, cache_hit: bool = False) -> None:
    with _dialogue_metrics_lock:
        _dialogue_metrics["count"] += 1
        _dialogue_metrics["total_seconds"] += duration_seconds
        _dialogue_metrics["max_seconds"] = max(_dialogue_metrics["max_seconds"], duration_seconds)
        _dialogue_metrics["last_seconds"] = duration_seconds
        if cache_hit:
            _dialogue_metrics["cache_hits"] += 1


def _get_dialogue_metrics_snapshot() -> dict:
    with _dialogue_metrics_lock:
        count = int(_dialogue_metrics["count"])
        total = float(_dialogue_metrics["total_seconds"])
        avg = (total / count) if count else 0.0
        return {
            "count": count,
            "avg_seconds": round(avg, 3),
            "max_seconds": round(float(_dialogue_metrics["max_seconds"]), 3),
            "last_seconds": (
                round(float(_dialogue_metrics["last_seconds"]), 3)
                if _dialogue_metrics["last_seconds"] is not None
                else None
            ),
            "cache_hits": int(_dialogue_metrics["cache_hits"]),
            "cache_size": len(_dialogue_response_cache),
            "cache_enabled": settings.DIALOGUE_DUPLICATE_CACHE_ENABLED,
            "cache_ttl_seconds": settings.DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS,
        }


def _start_dialogue() -> None:
    global _active_dialogues
    with _active_dialogues_lock:
        _active_dialogues += 1


def _finish_dialogue() -> None:
    global _active_dialogues
    with _active_dialogues_lock:
        _active_dialogues = max(0, _active_dialogues - 1)


def _get_active_dialogues() -> int:
    with _active_dialogues_lock:
        return _active_dialogues


def _release_dialogue_resources_when_done(worker: threading.Thread) -> None:
    """Libera recursos de diálogo cuando un procesamiento tardío finalmente termina."""
    grace_seconds = max(5, settings.DIALOGUE_TIMEOUT_RELEASE_GRACE_SECONDS)
    try:
        worker.join(timeout=grace_seconds)
        if worker.is_alive():
            print(
                "⚠️ Worker de diálogo sigue activo tras timeout y periodo de gracia. "
                "Se liberan recursos para evitar bloqueo de nuevas conversaciones."
            )
    finally:
        _finish_dialogue()
        app.state.active_dialogues = _get_active_dialogues()
        try:
            _dialogue_slots.release()
        except ValueError:
            # Evita que una doble liberación accidental tumbe el proceso.
            pass


async def _ciclo_aprendizaje_bd() -> None:
    """Mantiene al agente actualizándose con datos recientes de la base de datos."""
    intervalo = max(60, settings.DB_STUDY_INTERVAL_SECONDS)
    print(f"🔄 Ciclo de aprendizaje BD activo cada {intervalo} segundos")
    consecutive_failures = 0

    # Evita una ingesta inmediata al reiniciar: espera el primer ciclo programado.
    await asyncio.sleep(intervalo)

    while True:
        try:
            chats_activos = _get_active_dialogues()
            if chats_activos > 0:
                wait_seconds = min(intervalo, max(5, settings.DB_STUDY_IDLE_CHECK_SECONDS))
                print(
                    f"⏸️ Ingesta BD diferida por {chats_activos} conversación(es) activa(s). "
                    f"Revisando de nuevo en {wait_seconds}s"
                )
                await asyncio.sleep(wait_seconds)
                continue

            ingesta = asyncio.to_thread(ingestar_sistema_completo)
            if settings.DB_STUDY_MAX_RUN_SECONDS > 0:
                resultado = await asyncio.wait_for(ingesta, timeout=settings.DB_STUDY_MAX_RUN_SECONDS)
            else:
                resultado = await ingesta

            app.state.last_db_study_at = datetime.utcnow().isoformat()
            app.state.last_db_study_result = resultado
            app.state.last_db_study_error = None
            consecutive_failures = 0
            print(f"✅ Aprendizaje BD completado: {resultado}")
            wait_seconds = intervalo
        except asyncio.TimeoutError:
            app.state.last_db_study_error = (
                f"Ingesta excedió el tiempo máximo de {settings.DB_STUDY_MAX_RUN_SECONDS}s"
            )
            # No castigamos exponencialmente un timeout de una tarea que sigue completándose en background.
            consecutive_failures = 0
            wait_seconds = intervalo
            print(f"⚠️ {app.state.last_db_study_error}")
            print(f"⏳ Reintentando aprendizaje BD en {wait_seconds}s (timeout controlado, no se aplica backoff)")
        except Exception as exc:
            app.state.last_db_study_error = str(exc)
            consecutive_failures += 1
            backoff_factor = min(2 ** min(consecutive_failures, 4), 16)
            wait_seconds = intervalo * backoff_factor
            print(f"⚠️ Error en aprendizaje continuo desde BD: {exc}")
            print(f"⏳ Reintentando aprendizaje BD en {wait_seconds}s (fallos consecutivos: {consecutive_failures})")

        await asyncio.sleep(wait_seconds)


async def _ciclo_notificaciones_api() -> None:
    """Escucha en segundo plano la API de notificaciones para enriquecer contexto."""
    intervalo = max(10, settings.NOTIF_API_POLL_SECONDS)
    if not notification_listener.is_enabled():
        print("ℹ️ Listener de notificaciones deshabilitado (define NOTIF_API_BASE_URL para activarlo).")
        return

    initial_delay = max(0, settings.NOTIF_API_START_DELAY_SECONDS)
    if initial_delay > 0:
        print(f"⏳ Listener Notification API iniciará en {initial_delay}s para priorizar arranque de diálogo")
        await asyncio.sleep(initial_delay)

    print(f"🔔 Listener Notification API activo cada {intervalo} segundos")
        
    while True:
        try:
            # Evita competir con diálogos activos cuando el modo de pausa está habilitado.
            chats_activos = _get_active_dialogues()
            should_pause_for_dialogue = settings.NOTIF_PAUSE_DURING_DIALOGUE and chats_activos > 0
            if should_pause_for_dialogue:
                await asyncio.sleep(min(intervalo, max(5, settings.DB_STUDY_IDLE_CHECK_SECONDS)))
                continue

            # Fallback: incluso con la pausa deshabilitada, no competir bajo carga máxima.
            max_dialogues = max(1, settings.DIALOGUE_MAX_CONCURRENT)
            if chats_activos >= max_dialogues:
                await asyncio.sleep(min(intervalo, max(5, settings.DB_STUDY_IDLE_CHECK_SECONDS)))
                continue

            resultado = await notification_listener.pull_once()
            auto_reply_sent = await _process_auto_replies(resultado.get("auto_reply_candidates") or [])
            app.state.last_notification_poll_at = resultado.get("timestamp")
            app.state.last_notification_result = resultado
            app.state.last_notification_error = None
            app.state.last_auto_reply_sent = auto_reply_sent
            learned = resultado.get("learned", 0)
            skipped = resultado.get("skipped", 0)
            errors = resultado.get("errors", 0)
            if learned or errors or auto_reply_sent:
                print(
                    f"🔔 Notification API sync -> learned={learned}, "
                    f"skipped={skipped}, errors={errors}, auto_replies={auto_reply_sent}"
                )
        except Exception as exc:
            app.state.last_notification_error = str(exc)
            print(f"⚠️ Error en listener de notificaciones: {exc}")

        await asyncio.sleep(intervalo)


@app.on_event("startup")
async def startup_db_learning() -> None:
    """Lanza la tarea de aprendizaje continuo desde la base de datos."""
    if getattr(app.state, "db_study_task", None) is None:
        app.state.startup_connectivity = _run_startup_connectivity_checks()
        _log_startup_connectivity(app.state.startup_connectivity)
        if app.state.startup_connectivity.get("all_ok"):
            print("✅ Comprobador de conectividad inicial: OK")
        else:
            print("⚠️ Comprobador de conectividad inicial: hay servicios no alcanzables")
        app.state.last_db_study_at = None
        app.state.last_db_study_result = None
        app.state.last_db_study_error = None
        app.state.last_notification_poll_at = None
        app.state.last_notification_result = None
        app.state.last_notification_error = None
        app.state.last_auto_reply_sent = 0
        app.state.notification_warmup = None
        app.state.active_dialogues = 0
        app.state.notification_task = None
        if notification_listener.is_enabled():
            try:
                app.state.notification_warmup = await notification_listener.warmup_session()                
            except Exception as exc:
                app.state.notification_warmup = {"enabled": True, "logged_in": False, "error": str(exc)}
                print(f"⚠️ Warmup SOLIDSET listener falló: {exc}")
        app.state.db_study_task = asyncio.create_task(_ciclo_aprendizaje_bd())
        if settings.NOTIF_API_BACKGROUND_ENABLED:
            app.state.notification_task = asyncio.create_task(_ciclo_notificaciones_api())
        else:
            print("ℹ️ Listener Notification API en background desactivado por rendimiento (NOTIF_API_BACKGROUND_ENABLED=false)")


@app.on_event("shutdown")
async def shutdown_db_learning() -> None:
    """Detiene la tarea de aprendizaje continuo al apagar el servicio."""
    task = getattr(app.state, "db_study_task", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        app.state.db_study_task = None

    notification_task = getattr(app.state, "notification_task", None)
    if notification_task is not None:
        notification_task.cancel()
        with suppress(asyncio.CancelledError):
            await notification_task
        app.state.notification_task = None

# ============================================================
# MODELOS DE DATOS
# ============================================================

class ChatConversationRequest(BaseModel):
    session_id: str = Field(..., description="ID de la sesión de conversación")
    message: str = Field(..., description="Mensaje enviado por el usuario")
    user_id: str = Field(..., description="Username del usuario que está consultando en el sistema")
    resource_id: Optional[str] = Field(None, description="IDResource canónico del interlocutor")
    login_id: Optional[str] = Field(None, description="IDLogin de la sesión activa")
    canal_id: Optional[str] = Field(None, description="ID del canal actual (opcional)")
    generate_audio: bool = Field(False, description="Si se debe generar audio de la respuesta")

class ChatConversationResponse(BaseModel):
    session_id: str
    user_message: str
    agent_response: str
    audio_url: Optional[str] = None
    user_context_used: Optional[str] = None  # Para debugging


class UserFeedbackRequest(BaseModel):
    session_id: str = Field(..., description="ID de la sesión de conversación")
    user_id: str = Field(..., description="Username del usuario que aporta feedback")
    user_text: str = Field(..., description="Mensaje original del usuario")
    agent_response: str = Field(..., description="Respuesta del agente que se evalúa")
    corrected_response: Optional[str] = Field(None, description="Respuesta correcta o corrección del usuario")
    canal_id: Optional[str] = Field(None, description="ID del canal donde ocurrió la interacción")
    feedback_type: str = Field("explicit", description="Tipo de feedback: explicit o implicit")
    reason: Optional[str] = Field(None, description="Motivo del feedback o corrección")
    previous_user_text: Optional[str] = Field(None, description="Mensaje anterior del usuario para detectar repetición")
    update_profile: bool = Field(True, description="Si se debe actualizar el perfil dinámico del usuario")


class UserFeedbackResponse(BaseModel):
    status: str
    learned: bool
    profile_updated: bool
    reaction_signal: str
    topics: list[str] = []


class SolidSETReactionCaptureRequest(BaseModel):
    IDChat: int = Field(..., gt=0)
    IDUser: uuid.UUID
    IDChannel: uuid.UUID
    IDEmoji: str = Field(..., min_length=1, max_length=64)
    Counter: int = Field(..., ge=0)

    class Config:
        extra = "forbid"


class SolidSETReactionCaptureResponse(BaseModel):
    status: str
    learned: bool
    changed: bool
    signal: str
    IDChat: int
    IDAgentResource: uuid.UUID
    AgentName: str


class SysResourceIAConfiguration(BaseModel):
    Name: Optional[str] = Field(None, max_length=255)
    Stamp: Optional[datetime] = None
    IDResource: uuid.UUID
    active: bool = False

    class Config:
        extra = "forbid"


class SysResourceIAConfigurationStored(SysResourceIAConfiguration):
    ID: uuid.UUID


class SysResourceIAConfigurationResponse(BaseModel):
    status: str
    configuration: SysResourceIAConfigurationStored


class SysResourceIAIngestResponse(BaseModel):
    status: str
    sourceRows: int
    synchronized: int
    inserted: int
    updated: int
    skipped: int


class SysChatIAResourceIngestResponse(BaseModel):
    status: str
    sourceRows: int
    synchronized: int
    inserted: int
    existing: int
    skipped: int


class SysWorkRoomIngestResponse(BaseModel):
    status: str
    sourceRows: int
    synchronized: int
    inserted: int
    updated: int
    skipped: int


class SysLoginIngestResponse(BaseModel):
    status: str
    sourceRows: int
    synchronized: int
    inserted: int
    updated: int
    skipped: int


class MultiAgentDialogueRequest(BaseModel):
    IDWorkRoom: uuid.UUID
    IDSession: Optional[uuid.UUID] = None
    RawMessage: str = Field(..., min_length=1, max_length=5000)
    SelectedAgentResourceIds: list[uuid.UUID]
    SenderResourceId: Optional[uuid.UUID] = None
    SendToSolidSET: bool = False

    class Config:
        extra = "forbid"


class MultiAgentAnswer(BaseModel):
    IDAgentResource: uuid.UUID
    AgentName: str
    response: str
    sent: bool = False
    sendDetail: Optional[str] = None


class MultiAgentDialogueResponse(BaseModel):
    IDSession: uuid.UUID
    IDWorkRoom: uuid.UUID
    responses: list[MultiAgentAnswer]


class AgentKnowledgeRequest(BaseModel):
    IDWorkRoom: Optional[uuid.UUID] = None
    Title: Optional[str] = Field(None, max_length=255)
    KnowledgeText: str = Field(..., min_length=1, max_length=50000)
    Source: str = Field("manual", min_length=1, max_length=100)
    active: bool = True

    class Config:
        extra = "forbid"


class AgentKnowledgeResponse(BaseModel):
    ID: uuid.UUID
    IDResource: uuid.UUID
    IDWorkRoom: Optional[uuid.UUID] = None
    Title: Optional[str] = None
    KnowledgeText: str
    Source: str
    Stamp: datetime
    active: bool
    indexed: bool


class AgentWorkRoomConfiguration(BaseModel):
    active: bool = True
    response_order: int = Field(0, ge=0, le=1000)

    class Config:
        extra = "forbid"


class AgentWorkRoomConfigurationResponse(BaseModel):
    IDResource: uuid.UUID
    IDWorkRoom: uuid.UUID
    active: bool
    response_order: int


def _to_camel_alias(field_name: str) -> str:
    """Convierte PascalCase a camelCase respetando prefijos como ID."""
    acronym = re.match(r"^[A-Z]+(?=[A-Z][a-z]|$)", field_name)
    if acronym:
        prefix = acronym.group(0)
        return prefix.lower() + field_name[len(prefix):]
    return field_name[:1].lower() + field_name[1:]


class FrameworkMessageDTO(BaseModel):
    """Contrato receptor compatible con el DTO FrameworkMessage de Notification."""
    Stamp: Optional[datetime] = None
    Sender: Optional[dict[str, Any]] = None
    Destiny: Optional[dict[str, Any]] = None
    ExternalDestinations: Optional[list[dict[str, Any]]] = None
    ExcludeSenderUser: bool = False
    ExcludeSenderSession: bool = False
    IncludeSenderSession: bool = False
    Kind: Any = None
    IDNotification: Optional[str] = None
    RawMessage: Optional[str] = None
    RawMessageHtml: Optional[str] = None
    Importance: Any = None
    Priority: int = 0
    Modifiers: int = 0
    VisibilityLevel: Any = None
    MaskMessage: int = 0
    MessageMonitoring: int = 0
    Args: Optional[list[Any]] = None
    PointData: Any = None
    Chat: Any = None
    UserData: Any = None
    ChatReadData: Optional[list[Any]] = None
    ImportanceSettingData: Any = None
    NotificationSettingsData: Any = None
    MailData: Any = None
    CompanyData: Any = None
    VideoCallData: Any = None
    MeetingData: Any = None
    TaskData: Any = None
    ActivityData: Any = None
    Task: Any = None
    ScheduleActivity: Any = None
    ChatData: Any = None
    ChatTransferingData: Any = None
    ScheduledData: Any = None
    WorkRoomData: Any = None
    RecordData: Any = None
    ObjectContent: Any = None
    IDChatExtVars: Optional[str] = None
    Info: Optional[dict[str, str]] = None
    ExtraData: Optional[str] = None
    LinkData: Any = None
    TimeData: Any = None
    FeatureFlagData: Any = None
    RelatedRecordsData: Optional[list[Any]] = None
    ReminderData: Any = None
    AttentionCallNotificationLevel: Any = None
    AttentionCallNotify: bool = False
    NotifyDate: Optional[datetime] = None
    DebugData: Optional[list[Any]] = None
    TreatLaterNotifData: Any = None

    class Config:
        extra = "allow"
        populate_by_name = True
        alias_generator = _to_camel_alias


class SendMessageResultDTO(BaseModel):
    Result: int
    Message: FrameworkMessageDTO
    Error: Optional[str] = None


def _get_payload_value(payload: Optional[dict[str, Any]], *keys: str) -> Any:
    """Obtiene un valor de un objeto FrameworkMessage sin depender del casing."""
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _valid_framework_identifier(value: Any) -> Optional[str]:
    """Descarta identificadores vacíos que SolidSET usa como valor nulo."""
    normalized = str(value or "").strip()
    if not normalized or normalized == "00000000-0000-0000-0000-000000000000":
        return None
    return normalized


def _framework_message_to_dialogue(message: FrameworkMessageDTO) -> ChatConversationRequest:
    """Normaliza el contrato de Notification al contrato interno del diálogo."""
    sender = message.Sender or {}
    destiny = message.Destiny or {}
    chat = message.Chat if isinstance(message.Chat, dict) else {}
    # La identidad funcional del interlocutor en SolidSET es IDResource. IDLogin
    # sirve para autenticar, pero no debe sustituir al recurso cuando ambos llegan.
    user_id = _valid_framework_identifier(
        _get_payload_value(sender, "resource", "IDResource")
    ) or _valid_framework_identifier(
        _get_payload_value(sender, "login", "IDLogin")
    )
    resource_id = _valid_framework_identifier(
        _get_payload_value(sender, "resource", "IDResource")
    )
    canal_id = _valid_framework_identifier(
        _get_payload_value(destiny, "workRoom", "IDWorkRoom", "room", "IDRoom")
    ) or _valid_framework_identifier(
        _get_payload_value(chat, "idWorkRoom", "IDWorkRoom")
    )
    session_id = (
        resource_id
        or _valid_framework_identifier(_get_payload_value(sender, "session", "IDSession"))
        or _valid_framework_identifier(_get_payload_value(destiny, "session", "IDSession"))
        or _valid_framework_identifier(_get_payload_value(chat, "idChat2", "IDChat2"))
        or _valid_framework_identifier(_get_payload_value(sender, "conversationId", "IDConversation"))
        or _valid_framework_identifier(_get_payload_value(destiny, "conversationId", "IDConversation"))
        or canal_id
        or _valid_framework_identifier(message.IDNotification)
        or user_id
        or f"framework-dialogue-{uuid.uuid4()}"
    )
    raw_message = message.RawMessage
    if raw_message is None:
        raw_message = _get_payload_value(chat, "rawMessage", "RawMessage")
    anonymous_user_id = f"framework-user-{uuid.uuid4()}"
    return ChatConversationRequest(
        session_id=session_id,
        message=str(raw_message or ""),
        user_id=user_id or anonymous_user_id,
        resource_id=resource_id,
        login_id=_valid_framework_identifier(_get_payload_value(sender, "login", "IDLogin")),
        canal_id=canal_id,
        generate_audio=False,
    )

# ============================================================
# SEGURIDAD: FILTROS CONTRA PROMPT INJECTION
# ============================================================

PALABRAS_PROHIBIDAS = [
    "olvida", "ignora", "nuevas instrucciones", "system prompt", "contraseña",
    "password", "administrador", "admin", "root", "sysadmin", "cambia tu rol",
    "nuevo rol", "actúa como", "eres ahora", "desde ahora", "ignora tus",
    "sobreescribe", "reemplaza", "borra tus", "elimina tus", "reset",
    "reinicia", "desobedece", "salta", "bypass", "hack", "exploit"
]

PALABRAS_SQL_INYECCION = [
    "drop", "delete", "insert", "update", "alter", "truncate", 
    "exec", "execute", "xp_", "sp_", "union", "select.*into", 
    "bulk", "backup", "restore", "shutdown"
]

def detect_prompt_injection(text: str) -> bool:
    """Detecta intentos de inyección de prompts maliciosos."""
    text_lower = text.lower()
    for palabra in PALABRAS_PROHIBIDAS:
        if palabra in text_lower:
            return True
    return False

def detect_sql_injection(text: str) -> bool:
    """Detecta posibles inyecciones SQL en el texto del usuario."""
    text_lower = text.lower()
    # Si el usuario menciona SQL en contexto normal, no bloquear
    if "select" in text_lower or "from" in text_lower:
        for kw in PALABRAS_SQL_INYECCION:
            if kw in text_lower:
                return True
    return False

def detect_offensive_content(text: str) -> bool:
    """Detecta contenido ofensivo o inapropiado."""
    palabras_ofensivas = [
        "puta", "puto", "mierda", "cabrón", "cabrona", "hijo de puta",
        "pendejo", "pendeja", "chinga", "chingar", "verga", "culero",
        "culera", "malparido", "malparida", "gonorrea", "maricón",
        "maricon", "marica", "joder", "hostia", "coño", "cojones"
    ]
    text_lower = text.lower()
    for palabra in palabras_ofensivas:
        if palabra in text_lower:
            return True
    return False

# ============================================================
# MANEJADORES DE ERRORES
# ============================================================

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Maneja errores de validación de peticiones."""
    print("❌ Error de validación en la petición recibida:", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Maneja errores HTTP generales."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )

# ============================================================
# ENDPOINTS PRINCIPALES
# ============================================================

@app.post(
    "/api/v1/agent/solidset/workrooms/sync",
    response_model=SysWorkRoomIngestResponse,
)
def sync_solidset_workrooms() -> SysWorkRoomIngestResponse:
    """Sincroniza dbo.SysWorkRoom de SQL Server con PostgreSQL."""
    try:
        result = ingest_solidset_workrooms()
    except (pymssql.Error, psycopg.Error) as exc:
        print(f"❌ No se pudo sincronizar SysWorkRoom: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudieron sincronizar los canales de SolidSET.",
        ) from exc
    return SysWorkRoomIngestResponse(status="synchronized", **result)


@app.post(
    "/api/v1/agent/solidset/logins/sync",
    response_model=SysLoginIngestResponse,
)
def sync_solidset_logins() -> SysLoginIngestResponse:
    """Sincroniza dbo.SysLogin sin exponer credenciales en la respuesta."""
    try:
        result = ingest_solidset_logins()
    except (pymssql.Error, psycopg.Error) as exc:
        print(f"❌ No se pudo sincronizar SysLogin: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudieron sincronizar las cuentas de SolidSET.",
        ) from exc
    return SysLoginIngestResponse(status="synchronized", **result)

@app.post(
    "/api/v1/agent/solidset/agents/{agent_resource_id}/knowledge",
    response_model=AgentKnowledgeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_knowledge(
    agent_resource_id: uuid.UUID,
    request: AgentKnowledgeRequest,
) -> AgentKnowledgeResponse:
    """Persiste e indexa conocimiento exclusivo de un agente IA."""
    payload = (
        request.model_dump()
        if hasattr(request, "model_dump")
        else request.dict()
    )
    payload["IDResource"] = agent_resource_id
    try:
        saved = await asyncio.to_thread(save_agent_knowledge, payload)
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(status_code=404, detail="El agente indicado no existe.") from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo guardar el conocimiento.") from exc
    indexed = await asyncio.to_thread(agent.sistema_aprendizaje.aprender_conocimiento_agente, saved)
    return AgentKnowledgeResponse(**saved, indexed=indexed)


@app.put(
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
        raise HTTPException(status_code=404, detail="El agente indicado no existe.") from exc
    return AgentWorkRoomConfigurationResponse(**saved)

@app.post(
    "/api/v1/agent/solidset/multi-agent/dialogue",
    response_model=MultiAgentDialogueResponse,
)
async def handle_multi_agent_dialogue(
    request: MultiAgentDialogueRequest,
) -> MultiAgentDialogueResponse:
    """Ejecuta de forma independiente los agentes seleccionados por SolidSET."""
    selected = list(dict.fromkeys(request.SelectedAgentResourceIds))
    if not selected:
        raise HTTPException(status_code=422, detail="Selecciona al menos un agente.")
    if len(selected) > 10:
        raise HTTPException(status_code=422, detail="Se permiten como máximo 10 agentes por mensaje.")

    configured_agents = get_active_agents_for_workroom(request.IDWorkRoom, selected)
    if not configured_agents:
        raise HTTPException(
            status_code=404,
            detail="Ningún agente seleccionado está activo y asignado al canal.",
        )

    conversation_id = request.IDSession or uuid.uuid4()

    async def execute_one(configured_agent: dict[str, Any]) -> MultiAgentAnswer:
        agent_resource_id = str(configured_agent["IDResource"])
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
        await asyncio.to_thread(
            touch_agent_session,
            conversation_id,
            agent_resource_id,
            request.IDWorkRoom,
        )
        response_text = await asyncio.to_thread(
            orchestrator.invoke,
            session_id=isolated_session,
            user_text=request.RawMessage.strip(),
            user_id=str(request.SenderResourceId or "solidset-user"),
            canal_id=str(request.IDWorkRoom),
            message_metadata={
                "agent_resource_id": agent_resource_id,
                "agent_name": agent_name,
                "agent_knowledge": private_knowledge,
                "workroom_id": str(request.IDWorkRoom),
                "source": "solidset_multi_agent",
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
            send_detail = str(await asyncio.to_thread(
                solidset_send_chat_message.invoke,
                {
                    "canal_id": str(request.IDWorkRoom),
                    "mensaje": f"{agent_name}: {response_text}",
                    "confirm": True,
                    "generated_by_ia": True,
                    "agent_resource_id": agent_resource_id,
                },
            ))
            sent = send_detail.startswith("✅")
        return MultiAgentAnswer(
            IDAgentResource=uuid.UUID(agent_resource_id),
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

@app.post(
    "/api/v1/agent/solidset/chat-workroom/sync",
    response_model=SysChatIAResourceIngestResponse,
)
def sync_solidset_chat_resources() -> SysChatIAResourceIngestResponse:
    """Sincroniza recursos y salas de SQL Server con SysChatIAResource."""
    try:
        result = ingest_solidset_chat_resources()
    except (pymssql.Error, psycopg.Error) as exc:
        print(f"❌ No se pudo sincronizar SysChatIAResource: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudieron sincronizar las relaciones de chat.",
        ) from exc
    return SysChatIAResourceIngestResponse(status="synchronized", **result)

@app.post(
    "/api/v1/agent/solidset/resources/sync",
    response_model=SysResourceIAIngestResponse,
)
def sync_solidset_resources() -> SysResourceIAIngestResponse:
    """Sincroniza SysResources de SQL Server con SysResourceIA en PostgreSQL."""
    try:
        result = ingest_solidset_resources()
    except (pymssql.Error, psycopg.Error) as exc:
        print(f"❌ No se pudo sincronizar SysResourceIA: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudieron sincronizar los recursos entre SQL Server y PostgreSQL.",
        ) from exc
    return SysResourceIAIngestResponse(status="synchronized", **result)

@app.post(
    "/api/v1/agent/solidset/chat-configuration",
    response_model=SysResourceIAConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_solidset_chat_configuration(
    configuration: SysResourceIAConfiguration,
) -> SysResourceIAConfigurationResponse:
    """Guarda en PostgreSQL la configuración de chat IA recibida de SolidSET."""
    payload = (
        configuration.model_dump()
        if hasattr(configuration, "model_dump")
        else configuration.dict()
    )
    try:
        saved = save_sys_resource_ia(payload)
    except psycopg.Error as exc:
        print(f"❌ No se pudo guardar la configuración SysResourceIA: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo guardar la configuración en PostgreSQL.",
        ) from exc

    return SysResourceIAConfigurationResponse(
        status="saved",
        configuration=SysResourceIAConfigurationStored(**saved),
    )

@app.post(
    "/api/v1/agent/notification/framework-message",
    response_model=SendMessageResultDTO,
)
async def receive_framework_notification(message: FrameworkMessageDTO):
    print(message.model_dump_json(indent=2))

    """Recibe desde Notification un FrameworkMessage ya capturado y lo aprende en Qdrant."""

    payload = (
        message.model_dump(mode="json")
        if hasattr(message, "model_dump")
        else message.dict()
    )
    capture = notification_listener.capture_realtime_payload(payload)
    candidates = capture.get("auto_reply_candidates") or []
    if candidates:
        _schedule_auto_replies(candidates)
    if capture["errors"]:
        return SendMessageResultDTO(
            Result=2,  # UnexpectedException
            Message=message,
            Error=f"No se pudo indexar el mensaje en Qdrant: {capture['errors']} error(es).",
        )
    print(
        f"📥 FrameworkMessage aprendido={capture['learned']} "
        f"omitido={capture['skipped']} respuestas_programadas={len(candidates)}"
    )
    return SendMessageResultDTO(Result=0, Message=message, Error=None)

@app.post("/api/v1/agent/notification/frameworkHub/SendMessage")
async def capture_and_forward_framework_message(request: Request):
    print(request)
    """Captura el mensaje en Qdrant antes de reenviarlo al endpoint real de SolidSET."""
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {"RawMessage": raw_body.decode("utf-8", errors="replace")}

    capture = notification_listener.capture_realtime_payload(payload)

    upstream_base = (settings.NOTIF_API_BASE_URL or "").rstrip("/")
    if not upstream_base:
        raise HTTPException(status_code=503, detail={
            "message": "NOTIF_API_BASE_URL no está configurada para reenviar el mensaje.",
            "capture": capture,
        })

    upstream_url = f"{upstream_base}/frameworkHub/SendMessage"
    excluded_headers = {"host", "content-length", "connection", "transfer-encoding"}
    forward_headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in excluded_headers
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.NOTIF_API_TIMEOUT_SECONDS,
            verify=settings.NOTIF_API_VERIFY_TLS,
            follow_redirects=False,
        ) as client:
            upstream = await client.post(
                upstream_url,
                content=raw_body,
                headers=forward_headers,
                params=dict(request.query_params),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail={
            "message": f"El mensaje fue capturado, pero no pudo reenviarse a SolidSET: {exc}",
            "capture": capture,
        }) from exc

    # Este proxy es con frecuencia la primera entrada del mensaje. Debe programar
    # aquí la respuesta porque la notificación posterior tendrá la misma huella y
    # será correctamente descartada como duplicada. Solo se responde si SolidSET
    # aceptó primero el mensaje original.
    candidates = capture.get("auto_reply_candidates") or []
    if upstream.status_code < 400 and candidates:
        _schedule_auto_replies(candidates)
        print(
            f"📥 FrameworkHub reenviado status={upstream.status_code} "
            f"respuestas_programadas={len(candidates)}"
        )

    response_headers = {}
    if upstream.headers.get("content-type"):
        response_headers["content-type"] = upstream.headers["content-type"]
    response_headers["X-Agent-Capture-Learned"] = str(capture["learned"])
    response_headers["X-Agent-Replies-Scheduled"] = str(
        len(candidates) if upstream.status_code < 400 else 0
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )

@app.post("/api/v1/agent/dialogue", response_model=ChatConversationResponse)
def handle_dialogue(message: FrameworkMessageDTO):

    print(message.model_dump_json(indent=2))

    """
    Procesa un FrameworkMessage como diálogo con el agente.
    
    - Normaliza RawMessage, Sender y Destiny al contexto interno del diálogo
    - Valida la seguridad del mensaje
    - Obtiene el contexto del usuario (sistema de aprendizaje)
    - Procesa la consulta con el agente
    """
    chat_payload = message.Chat if isinstance(message.Chat, dict) else {}
    if message.RawMessage is None and _get_payload_value(chat_payload, "rawMessage", "RawMessage") is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="RawMessage o Chat.rawMessage es obligatorio para procesar un FrameworkMessage en /dialogue.",
        )

    req = _framework_message_to_dialogue(message)

    dialogue_started = False
    slot_acquired = False
    request_started_at = perf_counter()
    effective_canal_id = _resolve_effective_canal_id(req.canal_id, req.session_id)
    cache_key = _build_dialogue_cache_key(req.session_id, req.user_id, effective_canal_id, req.message)
    try:
        # --- 1. VALIDACIONES DE SEGURIDAD ---
        
        # Validar que el mensaje no esté vacío
        if not req.message or req.message.strip() == "":
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="⚠️ Por favor, escribe un mensaje para poder ayudarte."
            )
        
        # Validar largo del mensaje (prevenir abusos)
        if len(req.message) > 5000:
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message[:100] + "...",
                agent_response="⚠️ El mensaje es demasiado largo. Por favor, reduce tu consulta a menos de 5000 caracteres."
            )
        
        # Detectar inyección de prompts
        if detect_prompt_injection(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="🚫 Lo siento, no puedo procesar esa solicitud por políticas de seguridad. Si necesitas ayuda con tu consulta técnica, reformúlala de manera clara y directa."
            )
        
        # Detectar inyección SQL
        if detect_sql_injection(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="🔒 He detectado un intento de inyección SQL. Solo puedo ejecutar consultas de lectura (SELECT) seguras. ¿Qué información necesitas consultar? Por favor, especifica qué datos quieres ver."
            )
        
        # Detectar contenido ofensivo
        if detect_offensive_content(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="🤖 Por favor, mantén un tono respetuoso en la conversación. Estoy aquí para ayudarte con tus consultas técnicas sobre maquinaria y sistemas. ¿En qué puedo asistirte?"
            )

        cached_response_text = _get_cached_dialogue_response(cache_key)
        if cached_response_text:
            elapsed = perf_counter() - request_started_at
            _record_dialogue_metrics(elapsed, cache_hit=True)
            print(
                f"⚡ Cache HIT en diálogo (sesión: {req.session_id}) -> {elapsed:.2f}s"
            )
            result = ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response=cached_response_text,
            )

            if req.generate_audio and cached_response_text:
                try:
                    audio_path = text_to_speech(cached_response_text)
                    result.audio_url = f"/api/v1/agent/audio-response?file={os.path.basename(audio_path)}"
                except Exception as audio_error:
                    print(f"⚠️ Error generando audio desde cache: {audio_error}")

            return result

        # --- 2. PROCESAR CON EL AGENTE ---

        # Control de admisión: evita que exceso de carga bloquee conversaciones.
        slot_acquired = _dialogue_slots.acquire(timeout=max(0, settings.DIALOGUE_ADMISSION_TIMEOUT_SECONDS))
        if not slot_acquired:
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="⏳ El agente está atendiendo varias conversaciones en este momento. Intenta nuevamente en unos segundos."
            )
        
        # Registrar inicio del procesamiento
        print(f"📨 Procesando consulta de usuario {req.user_id} (sesión: {req.session_id})")
        print(f"   Mensaje: {req.message[:100]}...")

        _start_dialogue()
        dialogue_started = True
        app.state.active_dialogues = _get_active_dialogues()
        
        configured_timeout = settings.DIALOGUE_PROCESSING_TIMEOUT_SECONDS
        if configured_timeout <= 0:
            hard_timeout = settings.DIALOGUE_HARD_TIMEOUT_SECONDS
            # Si ambos quedan en 0 por configuración, usa un valor operativo seguro.
            processing_timeout = hard_timeout if hard_timeout > 0 else 300
            processing_timeout = max(5, processing_timeout)
            print(
                f"ℹ️ Modo bloqueante con timeout duro de seguridad: {processing_timeout}s "
                f"(sesión: {req.session_id})"
            )
        else:
            processing_timeout = max(5, configured_timeout)

        # Ejecutar el agente con timeout para mantener tiempos de respuesta controlados.
        response_holder = {}
        error_holder = {}

        def _run_agent_dialogue() -> None:
            try:
                response_holder["text"] = orchestrator.invoke(
                    session_id=req.session_id,
                    user_text=req.message,
                    user_id=req.user_id,
                    canal_id=effective_canal_id,
                    message_metadata={
                        "resource_id": req.resource_id or req.user_id,
                        "login_id": req.login_id,
                        "workroom_id": effective_canal_id,
                    },
                )
            except Exception as exc:
                error_holder["error"] = exc

        worker = threading.Thread(target=_run_agent_dialogue, daemon=True)
        worker.start()
        worker.join(timeout=processing_timeout)

        if worker.is_alive():
            print(
                f"⚠️ Timeout de conversación en sesión {req.session_id} tras {processing_timeout}s. "
                "Se devuelve respuesta controlada y se libera al terminar en segundo plano."
            )
            threading.Thread(
                target=_release_dialogue_resources_when_done,
                args=(worker,),
                daemon=True,
            ).start()
            dialogue_started = False
            slot_acquired = False
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="⏱️ La consulta está tomando más tiempo de lo esperado. Intenta de nuevo en unos segundos para mantener una respuesta ágil del sistema."
            )

        if "error" in error_holder:
            raise error_holder["error"]

        response_text = response_holder.get("text", "")

        _store_cached_dialogue_response(cache_key, response_text)
        
        # --- 3. CONSTRUIR RESPUESTA ---
        
        result = ChatConversationResponse(
            session_id=req.session_id,
            user_message=req.message,
            agent_response=response_text
        )
        
        # Generar audio si se solicita
        if req.generate_audio and response_text:
            try:
                audio_path = text_to_speech(response_text)
                result.audio_url = f"/api/v1/agent/audio-response?file={os.path.basename(audio_path)}"
            except Exception as audio_error:
                print(f"⚠️ Error generando audio: {audio_error}")
                # No falla la respuesta completa si el audio falla
        
        print(f"✅ Respuesta generada para usuario {req.user_id}")
        elapsed = perf_counter() - request_started_at
        _record_dialogue_metrics(elapsed)
        if elapsed >= max(0.1, settings.DIALOGUE_SLOW_LOG_SECONDS):
            print(
                f"🐢 Diálogo lento detectado (sesión: {req.session_id}) -> {elapsed:.2f}s, "
                f"mensaje='{req.message[:80]}'"
            )
        else:
            print(f"⏱️ Diálogo completado en {elapsed:.2f}s (sesión: {req.session_id})")
        return result
        
    except Exception as e:
        print(f"❌ Error crítico en /dialogue: {str(e)}")
        # Capturar error y devolver mensaje amigable
        return ChatConversationResponse(
            session_id=req.session_id,
            user_message=req.message,
            agent_response=f"⚠️ Lo siento, ocurrió un error al procesar tu consulta. Por favor, intenta nuevamente o contacta al administrador del sistema. (Error: {str(e)[:100]})"
        )
    finally:
        if dialogue_started:
            _finish_dialogue()
            app.state.active_dialogues = _get_active_dialogues()
        if slot_acquired:
            _dialogue_slots.release()


@app.post("/api/v1/agent/feedback", response_model=UserFeedbackResponse)
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
            reaction_signal=reaction.get("signal", "sin_senal"),
            topics=reaction.get("topics", []),
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error registrando feedback del usuario: {str(e)}"
        )


@app.post(
    "/api/v1/agent/solidset/reactions/capture",
    response_model=SolidSETReactionCaptureResponse,
    status_code=status.HTTP_201_CREATED,
)
def capture_solidset_agent_reaction(
    req: SolidSETReactionCaptureRequest,
) -> SolidSETReactionCaptureResponse:
    """Captura una reacción ya registrada en SolidSET y la aprende para su agente."""
    try:
        message = resolve_agent_message(req.IDChat)
    except (pymssql.Error, psycopg.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo resolver el mensaje reaccionado.",
        ) from exc
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El chat no existe o no fue emitido por un agente IA registrado.",
        )

    channel_id = req.IDChannel
    if channel_id.int == 0 and message.get("IDWorkRoom"):
        channel_id = uuid.UUID(str(message["IDWorkRoom"]))
    signal = classify_reaction(req.IDEmoji, req.Counter)
    reaction_data = {
        "IDChat": req.IDChat,
        "IDUser": req.IDUser,
        "IDChannel": channel_id,
        "IDEmoji": req.IDEmoji.strip(),
        "Counter": req.Counter,
        "Signal": signal,
        "IDAgentResource": message["IDAgentResource"],
        "AgentResponse": str(message.get("RawMessage") or ""),
    }
    try:
        _, changed = save_agent_reaction(reaction_data)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo persistir la reacción del agente.",
        ) from exc

    learned = False
    if changed and signal != "removed":
        learned = bool(agent.sistema_aprendizaje.aprender_actividad(Actividad(
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
                "agent_resource_id": str(message["IDAgentResource"]),
            },
        )))

    agent_name = _agent_visible_name(message)
    return SolidSETReactionCaptureResponse(
        status="captured",
        learned=learned,
        changed=changed,
        signal=signal,
        IDChat=req.IDChat,
        IDAgentResource=message["IDAgentResource"],
        AgentName=agent_name,
    )


@app.get("/api/v1/agent/audio-response")
def get_audio_file(file: str):
    """
    Devuelve un archivo de audio generado previamente.
    """
    file_path = os.path.join("/tmp", file)
    if os.path.exists(file_path):
        return FileResponse(
            file_path, 
            media_type="audio/mpeg",
            filename=file
        )
    raise HTTPException(status_code=404, detail="Archivo de audio no encontrado.")


@app.get("/api/v1/agent/history/{session_id}")
def get_chat_history(
    session_id: str,
    before: int = Query(
        0,
        ge=0,
        description="Cantidad de mensajes mas recientes a omitir antes de devolver resultados (cursor para scroll hacia atras)."
    ),
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="Cantidad maxima de mensajes a devolver por pagina."
    ),
):
    """
    Recupera el historial de conversación de una sesión específica desde Redis.
    Devuelve mensajes paginados para soportar scroll infinito:
    - before=0 trae los mensajes mas recientes.
    - before=N omite los N mas recientes y trae mensajes anteriores.
    """
    try:
        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        messages = []
        
        for msg in history.messages:
            # Identificar el rol
            role = "user" if msg.type in ["human", "user"] else "assistant"
            messages.append({
                "role": role, 
                "content": msg.content,
                "type": msg.type
            })

        total_messages = len(messages)
        end_index = max(0, total_messages - before)
        start_index = max(0, end_index - limit)
        paged_messages = messages[start_index:end_index]
        has_more = start_index > 0
        next_before = before + len(paged_messages) if has_more else None
        
        return {
            "session_id": session_id, 
            "messages": paged_messages,
            "total_messages": total_messages,
            "returned_messages": len(paged_messages),
            "pagination": {
                "before": before,
                "limit": limit,
                "has_more": has_more,
                "next_before": next_before,
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error recuperando historial: {str(e)}"
        )


@app.delete("/api/v1/agent/history/{session_id}")
def clear_chat_history(session_id: str):
    """
    Limpia el historial de una sesión específica.
    """
    try:
        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        history.clear()
        return {
            "session_id": session_id, 
            "status": "cleared",
            "message": "Historial eliminado correctamente"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error eliminando historial: {str(e)}"
        )


@app.get("/api/v1/agent/health")
def health_check():
    """
    Endpoint de salud para verificar que el servicio está funcionando.
    """
    return {
        "status": "healthy",
        "version": "2.0.0",
        "services": {
            "ollama": settings.OLLAMA_BASE_URL,
            "qdrant": settings.VECTOR_DB_URL,
            "redis": settings.REDIS_URL
        },
        "runtime": {
            "active_dialogues": _get_active_dialogues(),
            "dialogue_max_concurrent": settings.DIALOGUE_MAX_CONCURRENT,
            "dialogue_admission_timeout_seconds": settings.DIALOGUE_ADMISSION_TIMEOUT_SECONDS,
            "dialogue_processing_timeout_seconds": settings.DIALOGUE_PROCESSING_TIMEOUT_SECONDS,
            "dialogue_hard_timeout_seconds": settings.DIALOGUE_HARD_TIMEOUT_SECONDS,
            "dialogue_slow_log_seconds": settings.DIALOGUE_SLOW_LOG_SECONDS,
            "dialogue_metrics": _get_dialogue_metrics_snapshot(),
            "notification_listener_enabled": notification_listener.is_enabled(),
            "notification_background_enabled": settings.NOTIF_API_BACKGROUND_ENABLED,
            "auto_reply_enabled": settings.SOLIDSET_AUTO_REPLY_ENABLED,
            "auto_reply_require_mention": settings.SOLIDSET_AUTO_REPLY_REQUIRE_MENTION,
            "auto_reply_allow_self": settings.SOLIDSET_AUTO_REPLY_ALLOW_SELF,
            "auto_reply_mention_token": settings.SOLIDSET_AUTO_REPLY_MENTION_TOKEN,
            "auto_reply_max_per_cycle": settings.SOLIDSET_AUTO_REPLY_MAX_PER_CYCLE,
            "last_auto_reply_sent": getattr(app.state, "last_auto_reply_sent", 0),
            "notification_start_delay_seconds": settings.NOTIF_API_START_DELAY_SECONDS,
            "notification_poll_seconds": settings.NOTIF_API_POLL_SECONDS,
            "last_notification_poll_at": getattr(app.state, "last_notification_poll_at", None),
            "last_notification_result": getattr(app.state, "last_notification_result", None),
            "last_notification_error": getattr(app.state, "last_notification_error", None),
            "notification_warmup": getattr(app.state, "notification_warmup", None),
            "notification_api_metrics": notification_listener.get_api_metrics_snapshot(),
            "notification_learning_metrics": notification_listener.get_learning_metrics_snapshot(),
            "startup_connectivity": getattr(app.state, "startup_connectivity", None),
            "db_study_interval_seconds": settings.DB_STUDY_INTERVAL_SECONDS,
            "db_study_idle_check_seconds": settings.DB_STUDY_IDLE_CHECK_SECONDS,
            "db_study_max_run_seconds": settings.DB_STUDY_MAX_RUN_SECONDS,
            "last_db_study_at": getattr(app.state, "last_db_study_at", None),
            "last_db_study_error": getattr(app.state, "last_db_study_error", None)
        }
    }


@app.get("/api/v1/agent/evaluation/summary")
def get_agent_evaluation_summary():
    """
    Resumen operacional para evaluar:
    1) Calidad técnica de consumo API.
    2) Evolución del aprendizaje del agente en ciclos de escucha.
    """
    try:
        return {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "diagnostico_tecnico": {
                "dialogue": {
                    "active_dialogues": _get_active_dialogues(),
                    "max_concurrent": settings.DIALOGUE_MAX_CONCURRENT,
                    "admission_timeout_seconds": settings.DIALOGUE_ADMISSION_TIMEOUT_SECONDS,
                    "processing_timeout_seconds": settings.DIALOGUE_PROCESSING_TIMEOUT_SECONDS,
                    "hard_timeout_seconds": settings.DIALOGUE_HARD_TIMEOUT_SECONDS,
                    "metrics": _get_dialogue_metrics_snapshot(),
                },
                "api_runtime": notification_listener.get_api_metrics_snapshot(),
                "sql_retries": agent.sistema_aprendizaje.get_sql_retry_stats(),
                "last_notification_error": getattr(app.state, "last_notification_error", None),
            },
            "metricas_evolucion": {
                "learning_runtime": notification_listener.get_learning_metrics_snapshot(),
                "last_notification_result": getattr(app.state, "last_notification_result", None),
                "last_db_study_at": getattr(app.state, "last_db_study_at", None),
                "last_db_study_error": getattr(app.state, "last_db_study_error", None),
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error construyendo resumen de evaluación: {str(e)}"
        )


@app.get("/api/v1/agent/notification/recent-messages")
def get_recent_notification_messages(limit: int = Query(30, ge=1, le=200)):
    """
    Devuelve los últimos mensajes de canal/chat capturados por el listener.
    Sirve para validar visualmente si la Notification API está entregando mensajes reales.
    """
    try:
        return {
            "status": "ok",
            "listener_enabled": notification_listener.is_enabled(),
            "count": limit,
            "messages": notification_listener.get_recent_captured_messages(limit=limit),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo mensajes recientes de notification listener: {str(e)}"
        )


@app.get("/api/v1/agent/context/{user_id}")
def get_user_context(user_id: str):
    """
    Devuelve el contexto completo de un usuario (para debugging y validación).
    """
    try:
        from app.system.learning import SistemaAprendizaje
        sistema = SistemaAprendizaje()
        contexto = sistema.obtener_contexto_usuario(user_id)
        perfil_dinamico = sistema.obtener_perfil_dinamico(user_id)
        
        if contexto:
            return {
                "user_id": user_id,
                "context": contexto.dict(),
                "dynamic_profile": perfil_dinamico,
                "canales_count": len(contexto.canales_acceso),
                "actividades_count": len(contexto.actividades_recientes),
                "recursos_count": len(contexto.recursos_disponibles)
            }
        else:
            return {
                "user_id": user_id,
                "dynamic_profile": perfil_dinamico,
                "error": "Usuario no encontrado o sin contexto disponible"
            }
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error obteniendo contexto del usuario: {str(e)}"
        )


@app.get("/api/v1/agent/sql-retry-stats")
def get_sql_retry_stats():
    """
    Devuelve métricas acumuladas de reintentos SQL del sistema de aprendizaje.
    """
    try:
        return {
            "status": "ok",
            "sql_retry_stats": agent.sistema_aprendizaje.get_sql_retry_stats(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo métricas SQL: {str(e)}"
        )


@app.post("/api/v1/agent/sql-retry-stats/reset")
def reset_sql_retry_stats():
    """
    Reinicia métricas de reintentos SQL del sistema de aprendizaje.
    """
    try:
        previous = agent.sistema_aprendizaje.reset_sql_retry_stats()
        current = agent.sistema_aprendizaje.get_sql_retry_stats()
        return {
            "status": "ok",
            "message": "sql retry stats reset",
            "previous": previous,
            "current": current,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error reseteando métricas SQL: {str(e)}"
        )


# ============================================================
# ✅ NUEVO ENDPOINT: PROBAR CONECTIVIDAD CON SOLIDSET API
# ============================================================

@app.get("/api/v1/connectivity/solidset")
def test_solidset_connectivity():
    """
    Prueba de conectividad con la API SolidSET.
    Verifica que el endpoint Heartbeat esté accesible.
    
    Endpoints probados:
    - /RestApi/Heartbeat (recomendado para verificar comunicación)
    - /swagger/index.html (documentación)    
    """
    base_url = settings.SOLIDSET_RESTAPI_BASE_URL
    
    if not base_url:
        return {
            "status": "error",
            "message": "SOLIDSET_RESTAPI_BASE_URL no configurada en el entorno",
            "configured": False
        }
    
    results = {
        "status": "ok",
        "base_url": base_url,
        "timestamp": datetime.utcnow().isoformat(),
        "tests": {}
    }
    
    # Test 1: Heartbeat (END-POINT PRINCIPAL)
    heartbeat_result = _probe_http(base_url, "/RestApi/Heartbeat")
    results["tests"]["heartbeat"] = {
        "endpoint": f"{base_url}/RestApi/Heartbeat",
        "success": heartbeat_result.get("ok", False),
        "status_code": heartbeat_result.get("status_code"),
        "error": heartbeat_result.get("error")
    }
    
    # Test 2: Swagger (para verificar que la API está servida)
    swagger_result = _probe_http(base_url, "User/LoginJson")
    results["tests"]["swagger"] = {
        "endpoint": f"{base_url}User/LoginJson",
        "success": swagger_result.get("ok", False),
        "status_code": swagger_result.get("status_code"),
        "error": swagger_result.get("error")
    }

    # Test 3: OpenAPI spec (algunas instalaciones lo exponen en /openapi.json)
    openapi_result = _probe_http(base_url, "/openapi.json")
    results["tests"]["openapi"] = {
        "endpoint": f"{base_url}/openapi.json",
        "success": openapi_result.get("ok", False),
        "status_code": openapi_result.get("status_code"),
        "error": openapi_result.get("error")
    }
    
    # Determinar estado general
    heartbeat_ok = bool(results["tests"].get("heartbeat", {}).get("success", False))
    swagger_ok = bool(results["tests"].get("swagger", {}).get("success", False))
    openapi_ok = bool(results["tests"].get("openapi", {}).get("success", False))
    
    if heartbeat_ok:
        results["overall_status"] = "healthy"
        results["message"] = "✅ Comunicación exitosa con SolidSET API (Heartbeat OK)"
    elif swagger_ok:
        results["overall_status"] = "partial"
        results["message"] = "⚠️ SolidSET API accesible, pero Heartbeat no responde correctamente"
    elif openapi_ok:
        results["overall_status"] = "partial"
        results["message"] = "⚠️ SolidSET API OpenAPI accesible, pero servicios principales no responden"
    else:
        results["overall_status"] = "unreachable"
        results["message"] = "❌ No se pudo establecer comunicación con SolidSET API"
    
    return results


@app.get("/api/v1/connectivity/all")
def test_all_connectivity():
    """
    Prueba de conectividad con todos los servicios externos configurados.
    """
    base_url = settings.SOLIDSET_RESTAPI_BASE_URL
    
    results = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {}
    }
    
    # Test SolidSET
    if base_url:
        heartbeat_result = _probe_http(base_url, "/RestApi/Heartbeat")
        results["services"]["solidset_restapi"] = {
            "configured": True,
            "base_url": base_url,
            "heartbeat_ok": heartbeat_result.get("ok", False),
            "status_code": heartbeat_result.get("status_code"),
            "error": heartbeat_result.get("error")
        }
    else:
        results["services"]["solidset_restapi"] = {
            "configured": False,
            "error": "SOLIDSET_RESTAPI_BASE_URL no configurada"
        }
    
    # Test Ollama
    ollama_result = _probe_http(settings.OLLAMA_BASE_URL, "/api/tags")
    results["services"]["ollama"] = {
        "url": settings.OLLAMA_BASE_URL,
        "ok": ollama_result.get("ok", False),
        "status_code": ollama_result.get("status_code"),
        "error": ollama_result.get("error")
    }
    
    # Test Qdrant
    qdrant_result = _probe_http(settings.VECTOR_DB_URL, "/collections")
    results["services"]["qdrant"] = {
        "url": settings.VECTOR_DB_URL,
        "ok": qdrant_result.get("ok", False),
        "status_code": qdrant_result.get("status_code"),
        "error": qdrant_result.get("error")
    }
    
    # Test Redis (TCP)
    redis_host, redis_port = _extract_host_port_from_url(settings.REDIS_URL, 6379)
    redis_result = _probe_tcp(redis_host, redis_port)
    results["services"]["redis"] = {
        "url": settings.REDIS_URL,
        "ok": redis_result.get("ok", False),
        "error": redis_result.get("error")
    }
    
    return results


# ============================================================
# PUNTO DE ENTRADA PARA EJECUCIÓN DIRECTA
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
