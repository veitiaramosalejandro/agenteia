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
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList
from contextlib import suppress
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from time import perf_counter, time
from typing import Any, Optional
from urllib import error as urlerror
from urllib.parse import urlparse
from urllib.request import Request as URLRequest, urlopen
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
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
    agent_learning_enabled,
    deactivate_llm_provider_configuration,
    ensure_llm_provider_schema,
    ensure_agent_model_schema,
    ensure_agent_response_audit_schema,
    ensure_solidset_agent_resource_schema,
    ensure_payload_agent_workroom_assignments,
    get_active_agents_for_workroom,
    get_active_agent_identity_for_resource,
    get_agent_knowledge,
    get_llm_provider_configuration,
    get_agent_model_configuration,
    get_agent_model_configurations,
    get_solidset_instance,
    list_active_solidset_instances,
    list_llm_provider_configurations,
    save_agent_knowledge,
    save_llm_provider_configuration,
    save_agent_model_configuration,
    save_agent_response_audit,
    save_solidset_instance,
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
    verify_and_sync_solidset_agent_mapping,
)
from app.connectors.solidset_sql import (
    instance_context as solidset_sql_instance_context,
    test_connection as test_solidset_sql_connection,
)
from app.system.reaction_capture import (
    classify_reaction,
    get_agent_reinforcement_context,
    reaction_reward,
    resolve_agent_message,
    save_agent_reaction,
)
from app.system.schema import Actividad
from app.llm import LLMProviderConfig, ProviderRegistry, create_chat_model
from app.response_queue import AgentResponseQueue
from app.historical.producer import enqueue_next_batch
from app.historical.queue import HistoricalQueue
from app.historical.store import (
    approve_dry_run_cursors,
    ensure_schema as ensure_historical_schema,
    historical_points,
    list_audits as list_historical_audits,
    list_cursors as list_historical_cursors,
    mark_historical_deleted,
)

# ============================================================
# CONFIGURACIÓN DE LA APLICACIÓN
# ============================================================

OPENAPI_TAGS = [
    {"name": "Conversation", "description": "Direct requests and conversational agent execution."},
    {"name": "SolidSET Notifications", "description": "FrameworkMessage reception, preview, and capture."},
    {"name": "Asynchronous Responses", "description": "Response status tracking and queue metrics."},
    {"name": "Historical Ingestion", "description": "Dry runs, execution, auditing, and removal of historical knowledge."},
    {"name": "SolidSET Agents", "description": "Agents, workrooms, models, private knowledge, and multi-agent execution."},
    {"name": "SolidSET Configuration", "description": "SolidSET instances and master-data synchronization."},
    {"name": "LLM Providers", "description": "AI model and provider configuration."},
    {"name": "Learning and Feedback", "description": "Feedback, reactions, reinforcement signals, and learning evaluation."},
    {"name": "Audio, History and Context", "description": "Generated audio, conversation history, and user context."},
    {"name": "Observability", "description": "Health, metrics, recent messages, and internal diagnostics."},
    {"name": "Connectivity", "description": "Connectivity checks for configured external services."},
]


app = FastAPI(
    title="Agent API",
    description="Intelligent agent API integrated with SolidSET.",
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS,
)

# CORS para permitir conexiones desde el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _request_ip_details(request: Request) -> tuple[str, str]:
    """Obtiene la IP TCP y la cadena informada por proxies, sin confundirlas."""
    direct_ip = request.client.host if request.client else "unknown"
    forwarded_ip = (
        request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        or request.headers.get("x-real-ip", "").strip()
        or "-"
    )
    return direct_ip, forwarded_ip


_solidset_instance_cache_lock = threading.Lock()
_solidset_instance_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _resolve_request_solidset_instance(request: Request) -> dict[str, Any] | None:
    """Resuelve la instalación aun cuando la petición atraviesa Nginx."""
    direct_ip, forwarded_ip = _request_ip_details(request)
    instance_code = request.headers.get("x-solidset-instance", "").strip()
    request_host = str(getattr(getattr(request, "url", None), "hostname", "") or "")
    cache_key = "|".join((
        instance_code.lower(), forwarded_ip.lower(), direct_ip.lower(),
        request_host.lower(),
    ))
    with _solidset_instance_cache_lock:
        cached = _solidset_instance_cache.get(cache_key)
        if cached and cached[0] > time():
            return dict(cached[1])
    if instance_code:
        instance = get_solidset_instance(code=instance_code, source_ip=None)
        if instance is not None:
            with _solidset_instance_cache_lock:
                _solidset_instance_cache[cache_key] = (time() + 60, dict(instance))
        return instance

    # SourceIP puede contener una IP o el host público registrado para la
    # instalación. Detrás de Nginx, request.client es la IP del contenedor del
    # proxy, por lo que también se prueban X-Forwarded-For/X-Real-IP y Host.
    candidates = [forwarded_ip, direct_ip, request_host]
    seen: set[str] = set()
    for candidate in candidates:
        source = str(candidate or "").strip()
        if not source or source == "-" or source in seen:
            continue
        seen.add(source)
        instance = get_solidset_instance(source_ip=source)
        if instance is not None:
            with _solidset_instance_cache_lock:
                _solidset_instance_cache[cache_key] = (time() + 60, dict(instance))
            return instance

    # En una instalación con un único SolidSET activo no existe ambigüedad y
    # Nginx/Docker puede ocultar tanto el host público como la IP original.
    # Con varias instalaciones no se aplica este fallback: deben identificarse
    # por cabecera, IP o host para impedir respuestas en el sistema equivocado.
    active_instances = list_active_solidset_instances()
    if len(active_instances) == 1:
        instance = active_instances[0]
        with _solidset_instance_cache_lock:
            _solidset_instance_cache[cache_key] = (time() + 60, dict(instance))
        return instance
    return None


def _attach_solidset_instance(candidates: list[dict], instance: dict[str, Any]) -> None:
    for candidate in candidates:
        payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
        regional_sources = []
        for source_name in ("Info", "TimeData", "UserData"):
            source = payload.get(source_name)
            if isinstance(source, dict):
                regional_sources.append({str(key).lower(): value for key, value in source.items()})

        def regional_value(*keys: str) -> str:
            for source in regional_sources:
                for key in keys:
                    value = source.get(key.lower())
                    if value not in (None, ""):
                        return str(value).strip()
            return ""

        original_fingerprint = str(candidate.get("fingerprint") or "")
        candidate["fingerprint"] = f"{instance['ID']}:{original_fingerprint}"
        candidate["solidset_instance_id"] = str(instance["ID"])
        candidate["solidset_instance_code"] = str(instance["Code"])
        candidate["solidset_base_url"] = str(instance["BaseUrl"]).rstrip("/")
        candidate["solidset_notification_url"] = str(
            instance.get("NotificationUrl") or ""
        ).rstrip("/")
        candidate["country_code"] = (
            regional_value("country_code", "countryCode", "country")
            or str(instance.get("CountryCode") or "PT")
        ).upper()
        candidate["locale"] = (
            regional_value("locale", "culture", "language_tag")
            or str(instance.get("Locale") or "pt-PT")
        )
        requested_time_zone = regional_value(
            "time_zone", "timeZone", "timezone", "iana_time_zone", "ianaTimeZone"
        )
        if requested_time_zone:
            try:
                ZoneInfo(requested_time_zone)
            except (ZoneInfoNotFoundError, ValueError):
                requested_time_zone = ""
        candidate["time_zone"] = (
            requested_time_zone
            or str(instance.get("TimeZone") or "Europe/Lisbon")
        )


@app.middleware("http")
async def log_request_origin_ip(request: Request, call_next):
    """Muestra en consola el origen y resultado de cada petición HTTP."""
    started_at = perf_counter()
    direct_ip, forwarded_ip = _request_ip_details(request)
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        print(
            "🌐 API_REQUEST "
            f"ip={direct_ip} forwarded_ip={forwarded_ip} "
            f"method={request.method} endpoint={request.url.path} "
            f"status={status_code} duration_ms={elapsed_ms:.1f}",
            flush=True,
        )

# Instancia del agente
agent = MachiningAgent()
orchestrator = SolidSETOrchestrator(agent)
notification_listener = NotificationApiListener()
response_queue = AgentResponseQueue()
historical_queue = HistoricalQueue()

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
_response_status_lock = threading.Lock()
_response_status_fallback: dict[str, dict[str, Any]] = {}

_RESPONSE_DISPLAY_MESSAGES = {
    "queued": {"es": "Esperando…", "en": "Waiting…", "pt": "A aguardar…"},
    "processing": {"es": "Procesando…", "en": "Processing…", "pt": "A processar…"},
    "searching": {
        "es": "Buscando información…",
        "en": "Searching for information…",
        "pt": "A pesquisar informação…",
    },
    "thinking": {"es": "Pensando…", "en": "Thinking…", "pt": "A pensar…"},
    "sending": {
        "es": "Enviando respuesta…",
        "en": "Sending response…",
        "pt": "A enviar a resposta…",
    },
    "completed": {"es": "Respondido", "en": "Answered", "pt": "Respondido"},
    "failed": {
        "es": "No se pudo responder",
        "en": "Unable to respond",
        "pt": "Não foi possível responder",
    },
    "cancelled": {"es": "Cancelado", "en": "Cancelled", "pt": "Cancelado"},
}

_RESPONSE_STATUS_CODES = {
    "queued": 0,
    "processing": 1,
    "searching": 2,
    "thinking": 3,
    "sending": 4,
    "completed": 5,
    "failed": 6,
    "cancelled": 7,
}


def _response_display_messages(status_name: str) -> dict[str, str]:
    messages = _RESPONSE_DISPLAY_MESSAGES.get(status_name)
    if messages:
        return dict(messages)
    return {"es": status_name, "en": status_name, "pt": status_name}


def _localize_response_status(data: dict[str, Any], language: str) -> dict[str, Any]:
    localized = json.loads(json.dumps(data, ensure_ascii=False))
    lang = language if language in {"es", "en", "pt"} else "pt"
    messages = _response_display_messages(str(localized.get("status") or ""))
    localized["code"] = _RESPONSE_STATUS_CODES.get(
        str(localized.get("status") or ""), -1
    )
    localized["displayMessages"] = messages
    localized["displayMessage"] = messages[lang]
    localized["language"] = lang
    for agent_state in localized.get("agents") or []:
        agent_messages = _response_display_messages(str(agent_state.get("status") or ""))
        agent_state["code"] = _RESPONSE_STATUS_CODES.get(
            str(agent_state.get("status") or ""), -1
        )
        agent_state["displayMessages"] = agent_messages
        agent_state["displayMessage"] = agent_messages[lang]
    return localized


def _response_status_key(request_id: str) -> str:
    return f"machining:agent-response:v1:{request_id}"


def _response_chat_key(chat_id: str) -> str:
    return f"machining:agent-response-chat:v1:{chat_id}"


def _framework_message_chat_id(
    payload: dict[str, Any], candidates: list[dict[str, Any]]
) -> str:
    candidate_chat_id = next(
        (item.get("chat_id") for item in candidates if item.get("chat_id")), ""
    )
    chat_payload = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_payload_lower = {str(key).lower(): value for key, value in chat_payload.items()}
    return str(
        candidate_chat_id
        or chat_payload_lower.get("idchat2")
        or chat_payload_lower.get("idchat")
        or ""
    ).strip()


def _utc_status_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def _save_response_status(data: dict[str, Any]) -> None:
    request_id = str(data["requestId"])
    ttl = settings.AGENT_RESPONSE_STATUS_TTL_SECONDS
    serialized = json.dumps(data, ensure_ascii=False)
    try:
        _dialogue_redis.setex(_response_status_key(request_id), ttl, serialized)
        chat_id = str(data.get("chatId") or "").strip()
        if chat_id:
            _dialogue_redis.setex(_response_chat_key(chat_id), ttl, request_id)
    except redis.RedisError:
        with _response_status_lock:
            _response_status_fallback[request_id] = dict(data)


def _load_response_status(request_id: str) -> Optional[dict[str, Any]]:
    try:
        raw = _dialogue_redis.get(_response_status_key(request_id))
        return json.loads(raw) if raw else None
    except (redis.RedisError, json.JSONDecodeError):
        with _response_status_lock:
            value = _response_status_fallback.get(request_id)
            return dict(value) if value else None


def _load_response_status_by_chat(chat_id: str) -> Optional[dict[str, Any]]:
    try:
        request_id = _dialogue_redis.get(_response_chat_key(chat_id))
    except redis.RedisError:
        with _response_status_lock:
            request_id = next(
                (
                    key for key, value in reversed(list(_response_status_fallback.items()))
                    if str(value.get("chatId") or "") == chat_id
                ),
                None,
            )
    return _load_response_status(str(request_id)) if request_id else None


def _create_response_status(request_id: str, chat_id: str, candidate_count: int) -> dict[str, Any]:
    now = _utc_status_timestamp()
    data = {
        "requestId": request_id,
        "chatId": chat_id or None,
        "status": "queued",
        "code": _RESPONSE_STATUS_CODES["queued"],
        "displayMessage": _RESPONSE_DISPLAY_MESSAGES["queued"]["pt"],
        "displayMessages": _response_display_messages("queued"),
        "completed": False,
        "createdAt": now,
        "updatedAt": now,
        "completedAt": None,
        "candidateCount": candidate_count,
        "responseCount": 0,
        "error": None,
        "agents": [],
        "stageHistory": [{"status": "queued", "at": now}],
    }
    _save_response_status(data)
    return data


def _update_response_status(
    request_id: str,
    status_name: str,
    *,
    agent_resource_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    error: Optional[str] = None,
    response_count: Optional[int] = None,
    result: Optional[dict[str, Any]] = None,
) -> None:
    if not request_id:
        return
    data = _load_response_status(request_id)
    if data is None:
        return
    now = _utc_status_timestamp()
    display_messages = _response_display_messages(status_name)
    display = display_messages["pt"]
    if agent_resource_id:
        agents = data.setdefault("agents", [])
        agent_state = next(
            (item for item in agents if item.get("agentResourceId") == agent_resource_id),
            None,
        )
        if agent_state is None:
            agent_state = {"agentResourceId": agent_resource_id, "name": agent_name or ""}
            agents.append(agent_state)
        agent_state.update({
            "status": status_name,
            "code": _RESPONSE_STATUS_CODES.get(status_name, -1),
            "displayMessage": display,
            "displayMessages": display_messages,
            "updatedAt": now,
            "error": error,
        })
    data.update({
        "status": status_name,
        "code": _RESPONSE_STATUS_CODES.get(status_name, -1),
        "displayMessage": display,
        "displayMessages": display_messages,
        "updatedAt": now,
        "completed": status_name in {"completed", "failed", "cancelled"},
        "error": error,
    })
    if response_count is not None:
        data["responseCount"] = response_count
    if result is not None:
        data["result"] = result
    if data["completed"]:
        data["completedAt"] = now
    else:
        data["completedAt"] = None
    history = data.setdefault("stageHistory", [])
    if not history or history[-1].get("status") != status_name:
        history.append({"status": status_name, "at": now, "agentResourceId": agent_resource_id})
    _save_response_status(data)


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
        "necesito ", "quiero que ", "haz ", "genera ", "resume ", "analiza ",
        "compara ", "muéstrame ", "muestrame ", "indícame ", "indicame ",
        "what ", "how ", "when ", "where ", "who ", "why ", "can you ", "please ",
        "i need ", "i want ", "give me ", "tell me ", "show me ", "generate ",
        "summarize ", "analyse ", "analyze ", "compare ",
        "o que ", "como ", "quando ", "onde ", "quem ", "por que ", "pode ", "procura ",
        "preciso ", "quero que ", "faça ", "faz ", "gera ", "resume ", "analisa ",
        "compara ", "mostra ", "indica ", "diz-me ",
    )
    return text.startswith(starters)


def _is_conversational_continuation(raw_text: str) -> bool:
    text = " ".join(str(raw_text or "").strip().lower().split()).rstrip(".!… ")
    return text in {
        "sí", "si", "sim", "yes", "no", "não", "nao", "continúa", "continua",
        "continue", "prossegue", "pode continuar", "puedes continuar", "de acuerdo",
        "está bien", "esta bien", "ok", "vale", "correcto", "correto", "right",
    }


def _is_informational_learning_message(raw_text: str) -> bool:
    """Detect factual/declarative input that should be learned, not answered."""
    text = " ".join(str(raw_text or "").strip().split())
    lowered = text.lower()
    if not text or _looks_like_question_or_request(text) or _is_conversational_continuation(text):
        return False
    if any(term in lowered for term in (
        "gracias", "obrigado", "obrigada", "thank you", "thanks", "bom dia",
        "boa tarde", "boa noite", "buenos días", "buenas tardes", "buenas noches",
        "hello", "hola", "olá",
    )):
        return False
    explicit_learning = (
        "aprende ", "recuerda que ", "ten en cuenta ", "para tu conocimiento ",
        "te informo que ", "fica a saber ", "tem em conta ", "para teu conhecimento ",
        "para seu conhecimento ", "informo que ", "learn this ", "remember that ",
        "for your information ", "keep in mind ",
    )
    factual_patterns = (
        r"\b(?:es|son|era|fue|tiene|tienen|representa|pertenece)\b",
        r"\b(?:é|são|era|foi|tem|têm|representa|pertence)\b",
        r"\b(?:is|are|was|were|has|have|represents|belongs)\b",
        r"\b(?:19|20)\d{2}\b",
    )
    return (
        len(text) >= 160
        or "\n" in str(raw_text or "")
        or any(lowered.startswith(prefix) for prefix in explicit_learning)
        or any(re.search(pattern, lowered) for pattern in factual_patterns)
    )


def _learning_acknowledgement(raw_text: str) -> str:
    language = agent._detect_user_language(raw_text)
    return {
        "pt": "Agradeço a informação. Vou tê-la em conta.",
        "en": "Thank you for the information. I will take it into account.",
        "es": "Gracias por la información. La tendré en cuenta.",
    }[language]


def _quoted_reply_is_learning_only(candidate: dict[str, Any]) -> bool:
    """Classify an informative reply to a quoted chat as learning-only."""
    if not str(candidate.get("quoted_message") or "").strip():
        return False
    text = " ".join(str(candidate.get("message") or "").strip().lower().split())
    if not text or _looks_like_question_or_request(text):
        return False
    if _is_conversational_continuation(text):
        return False
    return True


def _candidate_is_learning_only(candidate: dict[str, Any]) -> bool:
    return _quoted_reply_is_learning_only(candidate) or _is_informational_learning_message(
        str(candidate.get("message") or "")
    )


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


def _schedule_auto_replies(candidates: list[dict], request_id: str = "") -> None:
    """Mantiene una referencia fuerte y registra fallos de la tarea en background."""
    if not candidates:
        return
    print(
        f"🤖 Auto-respuesta encolada; candidatos={len(candidates)}",
        flush=True,
    )
    if request_id:
        for candidate in candidates:
            candidate["response_request_id"] = request_id
    task = asyncio.create_task(_process_auto_replies(candidates))
    _auto_reply_background_tasks.add(task)

    def _completed(done: asyncio.Task) -> None:
        _auto_reply_background_tasks.discard(done)
        try:
            sent = done.result()
            print(f"🤖 Procesamiento de auto-respuesta finalizado; enviadas={sent}")
        except asyncio.CancelledError:
            _update_response_status(request_id, "cancelled")
            print("⚠️ Procesamiento de auto-respuesta cancelado")
        except Exception as exc:
            _update_response_status(request_id, "failed", error=str(exc))
            print(f"❌ Error no controlado procesando auto-respuesta: {exc}")

    task.add_done_callback(_completed)


def _enqueue_auto_replies(
    payload: dict[str, Any],
    instance: dict[str, Any],
    request_id: str,
    chat_id: str,
) -> str:
    """Publica una solicitud durable; la API nunca ejecuta el LLM directamente."""
    stream_id = response_queue.enqueue(request_id, chat_id, payload, instance)
    print(
        f"📥 Auto-respuesta en Redis Stream request={request_id} stream_id={stream_id} "
        "payload=FrameworkMessage",
        flush=True,
    )
    return stream_id


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


def _local_temporal_response(
    raw_text: str,
    *,
    time_zone: str,
    locale: str,
    country_code: str,
) -> Optional[str]:
    """Answers local date/time questions without allowing the LLM to invent a place."""
    text = " ".join((raw_text or "").strip().lower().split())
    asks_date = any(phrase in text for phrase in (
        "que dia é hoje", "que dia e hoje", "qual é a data", "qual e a data",
        "data de hoje", "qué día es hoy", "que día es hoy", "fecha de hoy",
        "what day is today", "what is today's date", "today's date",
    ))
    asks_time = any(phrase in text for phrase in (
        "que horas são", "que horas sao", "hora atual", "hora local",
        "qué hora es", "que hora es", "hora actual", "what time is it",
        "current time", "local time",
    ))
    if not asks_date and not asks_time:
        return None
    try:
        local_now = datetime.now(ZoneInfo(time_zone))
    except (ZoneInfoNotFoundError, ValueError):
        local_now = datetime.now(ZoneInfo("UTC"))
        time_zone = "UTC"

    # The incoming message always decides the response language. Locale only
    # selects regional conventions within that language (for example pt-PT).
    language = agent._detect_user_language(raw_text)
    country_names = {
        "PT": {"pt": "Portugal", "es": "Portugal", "en": "Portugal"},
        "ES": {"pt": "Espanha", "es": "España", "en": "Spain"},
        "BR": {"pt": "Brasil", "es": "Brasil", "en": "Brazil"},
    }
    country = country_names.get(country_code.upper(), {}).get(language, country_code.upper())
    weekdays = {
        "pt": ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"),
        "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
        "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    }
    months = {
        "pt": ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"),
        "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
        "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
    }
    language = language if language in weekdays else "en"
    weekday = weekdays[language][local_now.weekday()]
    month = months[language][local_now.month - 1]
    clock = local_now.strftime("%H:%M")
    if language == "pt":
        date_text = f"Hoje é {weekday}, {local_now.day} de {month} de {local_now.year}"
        time_text = f"A hora local é {clock}"
        suffix = f"em {country} (fuso horário {time_zone})"
        return f"{date_text} e são {clock}, {suffix}." if asks_date and asks_time else (
            f"{date_text}, {suffix}." if asks_date else f"{time_text}, {suffix}."
        )
    if language == "es":
        date_text = f"Hoy es {weekday}, {local_now.day} de {month} de {local_now.year}"
        time_text = f"La hora local es {clock}"
        suffix = f"en {country} (zona horaria {time_zone})"
        return f"{date_text} y son las {clock}, {suffix}." if asks_date and asks_time else (
            f"{date_text}, {suffix}." if asks_date else f"{time_text}, {suffix}."
        )
    date_text = f"Today is {weekday}, {month} {local_now.day}, {local_now.year}"
    time_text = f"The local time is {clock}"
    suffix = f"in {country} ({time_zone})"
    return f"{date_text}, and the time is {clock} {suffix}." if asks_date and asks_time else (
        f"{date_text} {suffix}." if asks_date else f"{time_text} {suffix}."
    )


def _direct_courtesy_response(
    raw_text: str,
    recipient_name: str = "",
) -> Optional[str]:
    """Responde cumplidos/agradecimientos directos sin ocupar SQL, RAG u Ollama."""
    text = " ".join((raw_text or "").strip().lower().split())
    if not text:
        return None
    display_name = " ".join(str(recipient_name or "").strip().split())
    try:
        uuid.UUID(display_name)
        display_name = ""
    except (ValueError, AttributeError):
        pass
    if display_name.lower() in {"desconocido", "unknown", "-"}:
        display_name = ""
    language = agent._detect_user_language(raw_text)
    greeting_name = f", {display_name}" if display_name else ""
    greeting_terms = {
        "hola", "hola como estas", "hola cómo estás", "buenos dias", "buenos días",
        "buenas tardes", "buenas noches", "olá", "ola", "bom dia", "boa tarde",
        "boa noite", "good morning", "good afternoon", "good evening", "hello", "hi",
    }
    if text.rstrip("!?., ") in greeting_terms:
        return {
            "pt": f"Olá{greeting_name}! É um prazer cumprimentá-lo. Como posso ajudar?",
            "en": f"Hello{greeting_name}! It is a pleasure to greet you. How can I help?",
            "es": f"¡Hola{greeting_name}! Es un placer saludarte. ¿En qué puedo ayudarte?",
        }[language]
    if _looks_like_question_or_request(text):
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
    if _candidate_is_learning_only(candidate) and not can_reply_direct:
        return "respuesta_citada_solo_aprendizaje"

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
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_lower = {str(key).lower(): value for key, value in chat.items()}
    chat_destinations = chat_lower.get("destiny")

    # Nueva señal explícita de SolidSET. Cuando Chat.destiny incluye
    # talkWithAgent, esa colección es autoritativa tanto en canales como en
    # meetings: solo los recursos IA (type=2) marcados con true responden. La
    # mera presencia del campo también impide caer en reglas antiguas y activar
    # por accidente otro agente del canal.
    has_talk_with_agent_flag = False
    selected_by_flag: list[tuple[int, str]] = []
    if isinstance(chat_destinations, list):
        for destination in chat_destinations:
            if not isinstance(destination, dict):
                continue
            lowered = {str(key).lower(): value for key, value in destination.items()}
            if "talkwithagent" not in lowered:
                continue
            has_talk_with_agent_flag = True
            flag = lowered.get("talkwithagent")
            talks_with_agent = (
                flag is True
                or (isinstance(flag, int) and flag == 1)
                or str(flag).strip().lower() in {"true", "1", "yes", "si", "sí"}
            )
            try:
                destination_type = int(lowered.get("type"))
            except (TypeError, ValueError):
                continue
            if not talks_with_agent or destination_type != 2:
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
            selected_by_flag.append((sequence, resource))
    if has_talk_with_agent_flag:
        selected_by_flag.sort(key=lambda item: item[0])
        return list(dict.fromkeys(resource for _, resource in selected_by_flag))

    if candidate.get("meeting_id"):
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

    # Chat privado propio: no hay Destiny.dests porque el usuario escribe en su
    # canal personal. Chat.destiny type=1 identifica al propietario y permite
    # conversar con su propio agente. Esta regla no aplica a meetings.
    if not candidate.get("meeting_id"):
        chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
        chat_lower = {str(key).lower(): value for key, value in chat.items()}
        channels = chat_lower.get("channels")
        private_channel = False
        if isinstance(channels, list):
            for channel in channels:
                if not isinstance(channel, dict):
                    continue
                lowered_channel = {
                    str(key).lower(): value for key, value in channel.items()
                }
                try:
                    if int(lowered_channel.get("channelkind")) == 1:
                        private_channel = True
                        break
                except (TypeError, ValueError):
                    continue
        if private_channel:
            private_destinations = chat_lower.get("destiny")
            owner_resources: list[str] = []
            if isinstance(private_destinations, list):
                for destination in private_destinations:
                    if not isinstance(destination, dict):
                        continue
                    lowered = {
                        str(key).lower(): value for key, value in destination.items()
                    }
                    try:
                        destination_type = int(lowered.get("type"))
                    except (TypeError, ValueError):
                        continue
                    if destination_type != 1:
                        continue
                    resource = str(
                        lowered.get("idresource") or lowered.get("resource") or ""
                    ).strip()
                    if resource and resource != str(uuid.UUID(int=0)):
                        owner_resources.append(resource)
            if owner_resources:
                return list(dict.fromkeys(owner_resources))

    selected: list[str] = []
    for key in ("SelectedAgentResourceIds", "selectedAgentResourceIds", "AgentResourceIds"):
        values = payload.get(key)
        if isinstance(values, list):
            selected.extend(str(value).strip() for value in values if value)
    destiny_resource = str(candidate.get("destiny_resource") or "").strip()
    if destiny_resource and destiny_resource != str(uuid.UUID(int=0)):
        selected.append(destiny_resource)
    return list(dict.fromkeys(value for value in selected if value))


def _human_reply_destination(candidate: dict) -> dict[str, str]:
    """Resuelve el type=1 de Chat.destiny al invertir humano -> IA en la respuesta."""
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_lower = {str(key).lower(): value for key, value in chat.items()}
    destinations = chat_lower.get("destiny")
    resource_table = chat_lower.get("resourcetable")
    sender_resource = str(candidate.get("sender_resource") or "").strip()
    humans: list[tuple[int, dict[str, str]]] = []
    if isinstance(destinations, list):
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            lowered = {str(key).lower(): value for key, value in destination.items()}
            try:
                destination_type = int(lowered.get("type"))
            except (TypeError, ValueError):
                continue
            if destination_type != 1:
                continue
            resource = str(lowered.get("idresource") or lowered.get("resource") or "").strip()
            login = str(lowered.get("idlogin") or lowered.get("login") or "").strip()
            if not resource:
                continue
            try:
                sequence = int(lowered.get("sequence") or 0)
            except (TypeError, ValueError):
                sequence = 0
            # Si existe más de un humano, el autor del mensaje tiene prioridad.
            priority = -1 if sender_resource and resource.lower() == sender_resource.lower() else sequence
            humans.append((priority, {
                "resource": resource,
                "login": login,
                "resource_name": str(lowered.get("username") or lowered.get("resourcename") or "").strip(),
            }))
    if humans:
        humans.sort(key=lambda item: item[0])
        selected_human = humans[0][1]
        if not selected_human["resource_name"] and isinstance(resource_table, list):
            for participant in resource_table:
                if not isinstance(participant, dict):
                    continue
                lowered = {str(key).lower(): value for key, value in participant.items()}
                participant_resource = str(
                    lowered.get("idresource") or lowered.get("resource") or ""
                ).strip()
                if participant_resource.lower() != selected_human["resource"].lower():
                    continue
                selected_human["resource_name"] = str(
                    lowered.get("username") or lowered.get("resourcename") or ""
                ).strip()
                break
        return selected_human
    return {
        "resource": sender_resource,
        "login": str(candidate.get("sender_login") or "").strip(),
        "resource_name": str(candidate.get("sender_name") or "").strip(),
    }


def _selected_agent_chat_destination(candidate: dict, agent_resource_id: str) -> dict[str, str]:
    """Conserva nombre/login del destino IA marcado con talkWithAgent."""
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    chat = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    destinations = {str(k).lower(): v for k, v in chat.items()}.get("destiny")
    if isinstance(destinations, list):
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            lowered = {str(key).lower(): value for key, value in destination.items()}
            resource = str(lowered.get("idresource") or lowered.get("resource") or "").strip()
            if resource.lower() != agent_resource_id.lower():
                continue
            return {
                "resource": resource,
                "login": str(lowered.get("idlogin") or lowered.get("login") or "").strip(),
                "resource_name": str(lowered.get("resourcename") or "").strip(),
            }
    return {"resource": agent_resource_id, "login": "", "resource_name": ""}


def _agent_visible_name(configured_agent: dict[str, Any]) -> str:
    """Construye la identidad pública del agente desde el nombre de su login."""
    resource_id = str(configured_agent.get("IDResource") or "").strip()
    full_name = str(configured_agent.get("FullName") or "").strip()
    fallback = str(configured_agent.get("Name") or resource_id).strip()
    identity = full_name or fallback or resource_id
    return f"{identity}".strip()


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
        instance = get_solidset_instance(
            code=str(candidate.get("solidset_instance_code") or "") or None,
            source_ip=None,
        )
        if not instance or not instance.get("DataAPI"):
            print("⚠️ Agente omitido: instância sem SolidSET Data API configurada")
            continue
        try:
            ensure_payload_agent_workroom_assignments(channel_id, selected)
            configured_agents = get_active_agents_for_workroom(channel_id, selected)
        except (ValueError, psycopg.Error) as exc:
            print(f"⚠️ No se pudo resolver agentes seleccionados para {channel_id}: {exc}")
            continue
        verified_mappings: dict[str, str] = {}
        for configured_agent in configured_agents:
            selected_resource_id = str(configured_agent["IDResource"])
            expected_agent_id = configured_agent.get("IDAgentResource")
            try:
                verification = verify_and_sync_solidset_agent_mapping(
                    selected_resource_id, expected_agent_id, instance
                )
            except (ValueError, pymssql.Error, psycopg.Error, RuntimeError) as exc:
                print(
                    "⚠️ Agente omitido: no se pudo verificar SysResource2Agent "
                    f"IDHumanResource={selected_resource_id}: {exc}"
                )
                continue
            verified_agent_id = str(
                verification.get("IDAgentResource") or ""
            ).strip()
            if not verification.get("verified") or not verified_agent_id:
                print(
                    "⚠️ Agente omitido: no existe relación activa en dbo.SysResource2Agent "
                    f"para IDHumanResource={selected_resource_id}"
                )
                continue
            verified_mappings[str(selected_resource_id).lower()] = verified_agent_id
        for configured_agent in configured_agents:
            agent_resource_id = str(configured_agent["IDResource"])
            cached_agent_resource_id = str(
                configured_agent.get("IDAgentResource") or ""
            ).strip()
            solidset_agent_resource_id = verified_mappings.get(
                agent_resource_id.lower(), ""
            )
            if cached_agent_resource_id != solidset_agent_resource_id:
                print(
                    "🔄 Identidad IA actualizada antes de responder "
                    f"human={agent_resource_id} "
                    f"previous={cached_agent_resource_id or '-'} "
                    f"current={solidset_agent_resource_id or '-'}"
                )
            if not solidset_agent_resource_id:
                print(
                    "⚠️ Agente omitido: no existe relación activa en dbo.SysResource2Agent "
                    f"para IDHumanResource={agent_resource_id}"
                )
                continue
            routed_candidate = dict(candidate)
            human_destination = _human_reply_destination(candidate)
            agent_chat_destination = _selected_agent_chat_destination(
                candidate, agent_resource_id
            )
            agent_chat_resource_name = str(
                agent_chat_destination.get("resource_name") or ""
            ).strip()
            if not agent_chat_resource_name:
                configured_resource_name = str(
                    configured_agent.get("Name") or "Agente IA"
                ).strip()
                agent_chat_resource_name = (
                    configured_resource_name
                    if configured_resource_name.lower().endswith("[ia]")
                    else f"{configured_resource_name} [IA]"
                )
            try:
                private_knowledge = get_agent_knowledge(agent_resource_id, channel_id)
            except (ValueError, psycopg.Error) as exc:
                print(f"⚠️ Conocimiento privado no disponible para {agent_resource_id}: {exc}")
                private_knowledge = ""
            try:
                reinforcement = get_agent_reinforcement_context(
                    agent_resource_id, channel_id
                )
            except (ValueError, psycopg.Error):
                reinforcement = ""
            routed_candidate.update({
                "fingerprint": f"{candidate.get('fingerprint')}:{agent_resource_id}",
                "agent_resource_id": agent_resource_id,
                "agent_identity_id": solidset_agent_resource_id,
                "agent_configuration_id": str(configured_agent.get("ID") or ""),
                "agent_name": _agent_visible_name(configured_agent),
                "agent_session_id": str(_candidate_session_id(candidate)),
                "agent_knowledge": private_knowledge,
                "agent_reinforcement": reinforcement,
                "addressed_to_agent": True,
                # Una vez que SolidSET seleccionó al agente, su respuesta debe
                # invertir la conversación: agente autenticado -> autor original.
                # La detección inicial de ``is_direct`` solo conoce la identidad
                # global configurada y puede no reconocer agentes dinámicos.
                "is_direct": bool(human_destination.get("resource")),
                "reply_resource": human_destination.get("resource", ""),
                "reply_login": human_destination.get("login", ""),
                "reply_resource_name": human_destination.get("resource_name", ""),
                "agent_chat_resource_name": agent_chat_resource_name,
                "agent_chat_login": agent_chat_destination.get("login", ""),
                "reply_destiny_inverted": True,
            })
            routed.append(routed_candidate)
    return routed


def _invoke_orchestrator_for_instance(instance_code: str, **kwargs: Any) -> str:
    if not instance_code:
        return orchestrator.invoke(**kwargs)
    instance = get_solidset_instance(code=instance_code, source_ip=None)
    if not instance or not instance.get("DataAPI"):
        raise RuntimeError("A instância SolidSET não tem uma SolidSET Data API configurada.")
    with solidset_sql_instance_context(instance):
        return orchestrator.invoke(**kwargs)


def _learn_agent_interaction(
    *,
    agent_resource_id: str,
    channel_id: str,
    session_id: str,
    user_text: str,
    response_text: str,
) -> None:
    """Guarda aprendizaje etiquetado; nunca queda visible para otro agente."""
    if not agent_learning_enabled(agent_resource_id, "system"):
        return
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


async def _process_auto_replies(
    candidates: list[dict], *, preview_only: bool = False
) -> int | list[dict[str, Any]]:
    print(
        f"🤖 Iniciando procesamiento de auto-respuesta; candidatos={len(candidates)}",
        flush=True,
    )
    response_request_id = str(
        next(
            (
                item.get("response_request_id")
                for item in candidates
                if item.get("response_request_id")
            ),
            "",
        )
    )
    if not preview_only and not settings.SOLIDSET_AUTO_REPLY_ENABLED:
        _update_response_status(
            response_request_id, "failed", error="La auto-respuesta está desactivada."
        )
        return 0
    if not preview_only and not settings.SOLIDSET_USER_ACTIONS_ENABLED:
        _update_response_status(
            response_request_id,
            "failed",
            error="El envío de acciones a SolidSET está desactivado.",
        )
        print("⚠️ Auto-reply SOLIDSET activo en config, pero SOLIDSET_USER_ACTIONS_ENABLED=false. No se enviarán respuestas.")
        return 0

    _update_response_status(response_request_id, "processing")
    candidates = _route_candidates_to_selected_agents(candidates)
    print(
        f"🤖 Enrutamiento de auto-respuesta completado; ejecuciones={len(candidates)}",
        flush=True,
    )
    if response_request_id and not candidates:
        _update_response_status(response_request_id, "completed", response_count=0)
    # Una selección explícita de SolidSET prevalece sobre el límite histórico
    # de una sola autorrespuesta, manteniendo un techo defensivo por mensaje.
    max_replies = min(
        10,
        max(1, settings.SOLIDSET_AUTO_REPLY_MAX_PER_CYCLE, len(candidates)),
    )
    sent = 0
    preview_payloads: list[dict[str, Any]] = []
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
        reply_login = str(
            candidate.get("reply_login") or candidate.get("sender_login") or ""
        ).strip()
        visibility_level = int(candidate.get("visibility_level", 1))
        meeting_id = str(candidate.get("meeting_id") or "").strip()
        meeting_code = str(candidate.get("meeting_code") or "").strip()
        message_kind = str(candidate.get("message_kind") or "ChatMessage")
        message_category = str(candidate.get("message_category") or "chat")
        importance = int(candidate.get("importance", 0))
        message_metadata = {
            "chat_id": candidate.get("chat_id"),
            "quoted_chat_id": candidate.get("quoted_chat_id"),
            "quoted_message": candidate.get("quoted_message"),
            "quoted_sender_resource": candidate.get("quoted_sender_resource"),
            "recipient_count": int(candidate.get("recipient_count", 0)),
            "importance": importance,
            "agent_resource_id": candidate.get("agent_resource_id"),
            "agent_name": candidate.get("agent_name"),
            "agent_knowledge": candidate.get("agent_knowledge"),
            "agent_reinforcement": candidate.get("agent_reinforcement"),
            "workroom_id": channel_id,
            "country_code": candidate.get("country_code") or "PT",
            "locale": candidate.get("locale") or "pt-PT",
            "time_zone": candidate.get("time_zone") or "Europe/Lisbon",
        }
        if not incoming_text or (not channel_id and not reply_resource):
            continue

        conversation_scope = reply_resource if is_direct else channel_id
        agent_resource_id = str(candidate.get("agent_resource_id") or "").strip()
        agent_identity_id = str(candidate.get("agent_identity_id") or "").strip()
        agent_name = str(candidate.get("agent_name") or agent_resource_id).strip()
        status_agent_id = agent_identity_id or agent_resource_id
        _update_response_status(
            response_request_id,
            "processing",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
        )
        conversation_id = str(
            candidate.get("chat_id")
            or candidate.get("agent_session_id")
            or conversation_scope
        )
        session_id = (
            f"solidset:{candidate.get('solidset_instance_id') or 'unscoped'}:"
            f"agent:{agent_resource_id}:room:{channel_id}:"
            f"conversation:{conversation_id}"
        )
        user_id = str(
            candidate.get("sender_resource")
            or candidate.get("sender_name")
            or settings.SOLIDSET_LOGIN_USERNAME
            or "solidset.agent"
        ).strip()
        learning_only = _candidate_is_learning_only(candidate)
        response_text = (
            _learning_acknowledgement(incoming_text)
            if is_direct and learning_only
            else _direct_courtesy_response(
                incoming_text,
                str(candidate.get("sender_name") or ""),
            )
            if is_direct
            else None
        )
        if response_text is None:
            response_text = _local_temporal_response(
                incoming_text,
                time_zone=str(candidate.get("time_zone") or "Europe/Lisbon"),
                locale=str(candidate.get("locale") or "pt-PT"),
                country_code=str(candidate.get("country_code") or "PT"),
            )
        if response_text is None:
            response_text = _weather_location_prompt(incoming_text)
        if response_text is not None:
            _update_response_status(
                response_request_id,
                "thinking",
                agent_resource_id=status_agent_id,
                agent_name=agent_name,
            )
        if response_text is None:
            try:
                external_query = _is_external_information_query(incoming_text)
                if external_query:
                    _update_response_status(
                        response_request_id,
                        "searching",
                        agent_resource_id=status_agent_id,
                        agent_name=agent_name,
                    )
                allowed_tools = (
                    {"google_web_search"}
                    if external_query
                    else {"query_sql_server", "get_db_schema"}
                )
                print(
                    f"🤖 Generando auto-respuesta con LLM channel={channel_id} "
                    f"target={'direct:' + reply_resource if is_direct else 'channel:' + channel_id} "
                    f"provider={settings.LLM_PROVIDER} model={settings.MODEL_NAME} "
                    f"base={settings.LLM_BASE_URL or settings.OLLAMA_BASE_URL} route="
                    f"{'external_web' if external_query else 'work_sql_rag'}"
                )
                _update_response_status(
                    response_request_id,
                    "thinking",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                )
                response_text = await asyncio.to_thread(
                    _invoke_orchestrator_for_instance,
                    str(candidate.get("solidset_instance_code") or ""),
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
                _update_response_status(
                    response_request_id,
                    "failed",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                    error=str(exc),
                )
                print(f"⚠️ Error generando auto-respuesta para canal {channel_id}: {exc}")
                continue

        response_text = (response_text or "").strip()
        if not _is_safe_auto_reply_output(response_text):
            response_text = (
                "No pude procesar correctamente tu mensaje en este momento. "
                "Por favor, inténtalo de nuevo en unos instantes."
            )

        try:
            _update_response_status(
                response_request_id,
                "sending",
                agent_resource_id=status_agent_id,
                agent_name=agent_name,
            )
            print(
                f"📤 Enviando auto-respuesta a SolidSET "
                f"base={candidate.get('solidset_base_url') or '-'} "
                f"agent_resource={agent_resource_id} meeting={meeting_id or '-'}",
                flush=True,
            )
            send_result = await asyncio.to_thread(
                solidset_send_chat_message.invoke,
                {
                    "canal_id": channel_id,
                    "mensaje": f"{response_text}",
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
                    "agent_identity_id": agent_identity_id,
                    "agent_chat_resource_name": candidate.get("agent_chat_resource_name"),
                    "agent_chat_login_id": candidate.get("agent_chat_login"),
                    "human_chat_resource_name": candidate.get("reply_resource_name"),
                    "solidset_base_url": candidate.get("solidset_base_url"),
                    "preview_only": preview_only,
                },
            )
            send_result_text = str(send_result)
            if preview_only:
                preview_payloads.append(json.loads(send_result_text))
                sent += 1
                continue
            if send_result_text.startswith("✅"):
                sent += 1
                _update_response_status(
                    response_request_id,
                    "completed",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                    response_count=sent,
                )
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
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            touch_agent_session,
                            candidate.get("agent_session_id"),
                            agent_resource_id,
                            channel_id,
                        ),
                        timeout=5,
                    )
                except Exception as exc:
                    print(
                        "⚠️ Respuesta enviada, pero no se pudo actualizar la "
                        f"sesión del agente: {exc}",
                        flush=True,
                    )
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            _learn_agent_interaction,
                            agent_resource_id=agent_resource_id,
                            channel_id=channel_id,
                            session_id=session_id,
                            user_text=incoming_text,
                            response_text=response_text,
                        ),
                        timeout=15,
                    )
                except Exception as exc:
                    print(
                        "⚠️ Respuesta enviada, pero no se pudo guardar su "
                        f"aprendizaje: {exc}",
                        flush=True,
                    )
            else:
                _update_response_status(
                    response_request_id,
                    "failed",
                    agent_resource_id=status_agent_id,
                    agent_name=agent_name,
                    error=send_result_text,
                    response_count=sent,
                )
                print(f"⚠️ Auto-reply no enviado en canal {channel_id}: {send_result_text}")
        except Exception as exc:
            _update_response_status(
                response_request_id,
                "failed",
                agent_resource_id=status_agent_id,
                agent_name=agent_name,
                error=str(exc),
                response_count=sent,
            )
            print(f"⚠️ Error enviando auto-respuesta a SOLIDSET (canal {channel_id}): {exc}")

    if response_request_id and not preview_only:
        _update_response_status(
            response_request_id,
            "completed" if sent > 0 or not candidates else "failed",
            error=None if sent > 0 or not candidates else "Ningún agente pudo enviar la respuesta.",
            response_count=sent,
        )
    return preview_payloads if preview_only else sent


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


def _probe_sql_server_connection(instance: dict[str, Any]) -> dict:
    """Checks the SQL Server connection persisted for one SolidSET instance."""
    try:
        result = test_solidset_sql_connection(instance)
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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

    try:
        configured_instances = list_active_solidset_instances()
        instances_error = ""
    except Exception as exc:
        configured_instances = []
        instances_error = str(exc)

    instance_checks = []
    for instance in configured_instances:
        base_url = str(instance.get("BaseUrl") or "").strip()
        notification_url = str(instance.get("NotificationUrl") or "").strip()

        def probe_configured_url(url: str, path: str, default_port: int) -> dict:
            if not url:
                return {"ok": False, "error": "url_nao_configurado", "url": url}
            candidates = [url.rstrip("/")]
            parsed = urlparse(url)
            if (
                (os.path.exists("/.dockerenv") or os.getenv("RUNNING_IN_DOCKER") == "1")
                and (parsed.hostname or "").lower() in {"localhost", "127.0.0.1"}
            ):
                docker_url = url.replace("localhost", "host.docker.internal").replace(
                    "127.0.0.1", "host.docker.internal"
                ).rstrip("/")
                if docker_url not in candidates:
                    candidates.insert(0, docker_url)
            last_probe = {"ok": False, "error": "sin_candidatos", "url": url}
            for candidate_url in candidates:
                tcp = _probe_tcp(*_extract_host_port_from_url(candidate_url, default_port))
                http = _probe_http(
                    candidate_url,
                    path,
                    verify_tls=settings.NOTIF_API_VERIFY_TLS,
                )
                last_probe = {
                    "ok": bool(tcp.get("ok") and http.get("ok")),
                    "configured_url": url,
                    "effective_url": candidate_url,
                    "tcp": tcp,
                    "http": http,
                }
                if last_probe["ok"]:
                    break
            return last_probe

        instance_checks.append({
            "id": str(instance.get("ID") or ""),
            "code": str(instance.get("Code") or ""),
            "name": str(instance.get("Name") or ""),
            "source_ip": str(instance.get("SourceIP") or ""),
            "base_url": base_url,
            "notification_url": notification_url,
            "solidset": probe_configured_url(base_url, "/RestApi/Heartbeat", 80),
            "notification": (
                probe_configured_url(notification_url, "/api/Request", 443)
                if notification_url
                else {"ok": True, "skipped": True, "error": "NotificationUrl_nao_configurado"}
            ),
            "database": _probe_sql_server_connection(instance),
        })
    checks["solidset_instances"] = {
        "configured": bool(instance_checks),
        "error": instances_error or None,
        "instances": instance_checks,
    }

    checks["sql_server"] = {
        "configured": any(bool(item.get("DataAPI")) for item in configured_instances),
        "instances": [
            {"code": item["code"], "connection": item["database"]}
            for item in instance_checks
        ],
    }

    db_url = os.getenv("DB_URL", "")
    pg_host, pg_port = _extract_host_port_from_url(db_url, 5432)
    checks["postgres_timescaledb"] = {
        "configured": bool(db_url),
        "tcp": _probe_tcp(pg_host, pg_port) if db_url else {"ok": False, "error": "DB_URL_nao_configurado"},
    }

    all_ok = True
    for service_name, service_data in checks.items():
        if service_name == "solidset_instances":
            if not service_data.get("configured") or service_data.get("error"):
                all_ok = False
            for instance in service_data.get("instances", []):
                if not instance.get("solidset", {}).get("ok", False):
                    all_ok = False
                notification = instance.get("notification", {})
                if not notification.get("ok", False) and not notification.get("skipped"):
                    all_ok = False
                if not instance.get("database", {}).get("ok", False):
                    all_ok = False
            continue
        if service_name == "sql_server":
            continue
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

    postgres = checks.get("postgres_timescaledb", {})
    db_url = os.getenv("DB_URL", "")
    print(f"   - PostgreSQL/TimescaleDB URL: {db_url or 'DB_URL_no_configurada'}")
    print(f"     • TCP: {_probe_to_text(postgres.get('tcp', {}))}")

    configured = checks.get("solidset_instances", {})
    print("   - Instancias SolidSET configuradas en PostgreSQL:")
    if configured.get("error"):
        print(f"     • ERROR consultando SysSolidSETInstance: {configured['error']}")
    elif not configured.get("instances"):
        print("     • Ninguna instancia activa configurada")
    for instance in configured.get("instances", []):
        print(
            f"     • [{instance.get('code') or '-'}] {instance.get('name') or '-'} "
            f"SourceIP={instance.get('source_ip') or '-'}"
        )
        solidset = instance.get("solidset", {})
        print(
            f"       SolidSET: {solidset.get('configured_url') or instance.get('base_url') or '-'} "
            f"-> {solidset.get('effective_url') or '-'}: {_probe_to_text(solidset)}"
        )
        notification = instance.get("notification", {})
        print(
            f"       Notification: {notification.get('configured_url') or instance.get('notification_url') or '-'} "
            f"-> {notification.get('effective_url') or '-'}: {_probe_to_text(notification)}"
        )
        print(f"       SQL Server: {_probe_to_text(instance.get('database', {}))}")


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
        try:
            await asyncio.to_thread(ensure_llm_provider_schema)
            await asyncio.to_thread(ensure_solidset_agent_resource_schema)
            await asyncio.to_thread(ensure_agent_model_schema)
            await asyncio.to_thread(ensure_agent_response_audit_schema)
            await asyncio.to_thread(ensure_historical_schema)
        except psycopg.Error as exc:
            print(f"⚠️ No se pudo asegurar SysLLMProviderConfiguration: {exc}")
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
    session_id: str = Field(..., description="Conversation session ID")
    message: str = Field(..., description="Message submitted by the user")
    user_id: str = Field(..., description="Username of the user making the request")
    resource_id: Optional[str] = Field(None, description="Canonical IDResource of the participant")
    login_id: Optional[str] = Field(None, description="IDLogin of the active session")
    canal_id: Optional[str] = Field(None, description="Current workroom ID (optional)")
    generate_audio: bool = Field(False, description="Whether an audio response should be generated")

class ChatConversationResponse(BaseModel):
    session_id: str
    user_message: str
    agent_response: str
    audio_url: Optional[str] = None
    user_context_used: Optional[str] = None  # Para debugging


class UserFeedbackRequest(BaseModel):
    session_id: str = Field(..., description="Conversation session ID")
    user_id: str = Field(..., description="Username of the user providing feedback")
    user_text: str = Field(..., description="Original user message")
    agent_response: str = Field(..., description="Agent response being evaluated")
    corrected_response: Optional[str] = Field(None, description="Expected response or user correction")
    canal_id: Optional[str] = Field(None, description="Workroom ID where the interaction occurred")
    feedback_type: str = Field("explicit", description="Feedback type: explicit or implicit")
    reason: Optional[str] = Field(None, description="Reason for the feedback or correction")
    previous_user_text: Optional[str] = Field(None, description="Previous user message used to detect repetition")
    update_profile: bool = Field(True, description="Whether the dynamic user profile should be updated")


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
    reward: float
    IDChat: int
    IDAgentResource: uuid.UUID
    AgentName: str


class SysResourceIAConfiguration(BaseModel):
    Name: Optional[str] = Field(None, max_length=255)
    Stamp: Optional[datetime] = None
    IDResource: uuid.UUID
    IDAgentResource: Optional[uuid.UUID] = None
    active: bool = False

    class Config:
        extra = "forbid"


class SysResourceIAConfigurationStored(SysResourceIAConfiguration):
    ID: uuid.UUID


class SysResourceIAConfigurationResponse(BaseModel):
    status: str
    configuration: SysResourceIAConfigurationStored


class SolidSETDataAPIConfiguration(BaseModel):
    BaseUrl: str = Field(..., min_length=8, max_length=500)
    APIKey: Optional[str] = Field(None, max_length=8000)
    TimeoutSeconds: int = Field(120, ge=5, le=3600)
    MaxRows: int = Field(5000, ge=1, le=100000)
    VerifyTLS: bool = True
    active: bool = True

    class Config:
        extra = "forbid"


class SolidSETDataAPIStored(BaseModel):
    BaseUrl: str
    TimeoutSeconds: int
    MaxRows: int
    VerifyTLS: bool
    active: bool
    APIKeyConfigured: bool = True


class SolidSETInstanceConfiguration(BaseModel):
    Code: str = Field(..., min_length=1, max_length=80)
    Name: str = Field(..., min_length=1, max_length=255)
    BaseUrl: str = Field(..., min_length=8, max_length=500)
    NotificationUrl: Optional[str] = Field(None, max_length=500)
    SourceIP: Optional[str] = Field(None, max_length=255)
    CountryCode: str = Field("PT", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    Locale: str = Field("pt-PT", min_length=2, max_length=20, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    TimeZone: str = Field("Europe/Lisbon", min_length=1, max_length=80)
    active: bool = True
    DataAPI: Optional[SolidSETDataAPIConfiguration] = None

    class Config:
        extra = "forbid"


class SolidSETInstanceStored(SolidSETInstanceConfiguration):
    ID: uuid.UUID
    CreatedAt: datetime
    UpdatedAt: datetime

    DataAPI: Optional[SolidSETDataAPIStored] = None


class SolidSETInstanceConfigurationResponse(BaseModel):
    status: str
    configuration: SolidSETInstanceStored


class SolidSETDataAPIConnectionTestResponse(BaseModel):
    status: str
    instanceCode: str
    connected: bool
    databaseName: Optional[str] = None
    serverVersion: Optional[str] = None
    adapterCode: str
    hasSysResource2Agent: bool


class LLMProviderConfiguration(BaseModel):
    Code: str = Field(..., min_length=1, max_length=80)
    Name: str = Field(..., min_length=1, max_length=255)
    Provider: str = Field(..., min_length=1, max_length=40)
    Model: str = Field(..., min_length=1, max_length=255)
    BaseUrl: Optional[str] = Field(None, max_length=500)
    APIKey: Optional[str] = Field(None, max_length=8000)
    Temperature: float = Field(0.5, ge=0, le=2)
    MaxOutputTokens: int = Field(1024, gt=0, le=131072)
    TimeoutSeconds: int = Field(60, gt=0, le=3600)
    AzureEndpoint: Optional[str] = Field(None, max_length=500)
    AzureApiVersion: Optional[str] = Field(None, max_length=80)
    AzureDeployment: Optional[str] = Field(None, max_length=255)
    IDResource: Optional[uuid.UUID] = None
    IsDefault: bool = False
    active: bool = True

    class Config:
        extra = "forbid"


class LLMProviderConfigurationStored(BaseModel):
    ID: uuid.UUID
    Code: str
    Name: str
    Provider: str
    Model: str
    BaseUrl: Optional[str] = None
    HasAPIKey: bool
    Temperature: float
    MaxOutputTokens: int
    TimeoutSeconds: int
    AzureEndpoint: Optional[str] = None
    AzureApiVersion: Optional[str] = None
    AzureDeployment: Optional[str] = None
    IDResource: Optional[uuid.UUID] = None
    IsDefault: bool
    active: bool
    CreatedAt: datetime
    UpdatedAt: datetime


class LLMProviderConfigurationResponse(BaseModel):
    status: str
    configuration: LLMProviderConfigurationStored


class AgentIAModelConfiguration(BaseModel):
    ProviderCode: str = Field(..., min_length=1, max_length=80)
    Role: str = Field("general", min_length=1, max_length=80)
    LocalExecution: bool = True
    TrainingMode: str = Field("rag_reinforcement", pattern="^(rag_reinforcement|rag_only|disabled)$")
    LearnFromOwner: bool = True
    LearnFromSystem: bool = True
    LearnFromReactions: bool = True
    Capabilities: list[str] = Field(default_factory=lambda: ["general"], min_length=1)
    Priority: int = Field(100, ge=0, le=10000)
    IsDefault: bool = False
    active: bool = True

    class Config:
        extra = "forbid"


class AgentIAModelStored(AgentIAModelConfiguration):
    ID: uuid.UUID
    IDResource: uuid.UUID
    IDProviderConfiguration: uuid.UUID
    CreatedAt: datetime
    UpdatedAt: datetime


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
    SolidSETInstanceCode: Optional[str] = Field(None, max_length=80)

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
    requestId: Optional[str] = None
    status: Optional[str] = None
    statusUrl: Optional[str] = None


class ChatQuestionSuggestionItem(BaseModel):
    id: str
    text: str


class ChatQuestionSuggestionResponse(BaseModel):
    requestId: str
    questionChatId: str
    status: str
    code: int
    language: str
    suggestions: list[ChatQuestionSuggestionItem]
    statusUrl: str


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
    translated_errors = []
    validation_messages = {
        "missing": "Campo obrigatório.",
        "string_type": "O valor deve ser uma cadeia de caracteres.",
        "int_type": "O valor deve ser um número inteiro.",
        "bool_type": "O valor deve ser verdadeiro ou falso.",
        "uuid_parsing": "O valor deve ser um UUID válido.",
        "url_parsing": "O valor deve ser um URL válido.",
        "json_invalid": "O corpo do pedido contém JSON inválido.",
    }
    for error in exc.errors():
        translated = dict(error)
        error_type = str(error.get("type") or "")
        translated["msg"] = validation_messages.get(
            error_type,
            "O valor fornecido não é válido para este campo.",
        )
        translated_errors.append(translated)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": translated_errors},
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
    tags=["SolidSET synchronization"],
    summary="Synchronize workrooms from one SolidSET instance",
)
def sync_solidset_workrooms(instanceCode: str = Query(...)) -> SysWorkRoomIngestResponse:
    """Synchronizes dbo.SysWorkRoom using the SQL Server connection selected by instanceCode."""
    try:
        instance = get_solidset_instance(code=instanceCode, source_ip=None)
        if not instance or not instance.get("DataAPI"):
            raise HTTPException(status_code=404, detail="A instância ou a SolidSET Data API não existe.")
        result = ingest_solidset_workrooms(instance)
    except (pymssql.Error, psycopg.Error, RuntimeError) as exc:
        print(f"❌ No se pudo sincronizar SysWorkRoom: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível sincronizar os canais do SolidSET.",
        ) from exc
    return SysWorkRoomIngestResponse(status="synchronized", **result)


@app.post(
    "/api/v1/agent/solidset/logins/sync",
    response_model=SysLoginIngestResponse,
    tags=["SolidSET synchronization"],
    summary="Synchronize logins from one SolidSET instance",
)
def sync_solidset_logins(instanceCode: str = Query(...)) -> SysLoginIngestResponse:
    """Synchronizes dbo.SysLogin without exposing credentials in the response."""
    try:
        instance = get_solidset_instance(code=instanceCode, source_ip=None)
        if not instance or not instance.get("DataAPI"):
            raise HTTPException(status_code=404, detail="A instância ou a SolidSET Data API não existe.")
        result = ingest_solidset_logins(instance)
    except (pymssql.Error, psycopg.Error, RuntimeError) as exc:
        print(f"❌ No se pudo sincronizar SysLogin: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível sincronizar as contas do SolidSET.",
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
        raise HTTPException(status_code=404, detail="O agente indicado não existe.") from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível guardar o conhecimento.") from exc
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
        raise HTTPException(status_code=404, detail="O agente indicado não existe.") from exc
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
        raise HTTPException(status_code=422, detail="Selecione, pelo menos, um agente.")
    if len(selected) > 10:
        raise HTTPException(status_code=422, detail="É permitido um máximo de 10 agentes por mensagem.")

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
            raise HTTPException(status_code=404, detail="A instância SolidSET não existe ou está inativa.")

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
                    "agent_identity_id": agent_identity_id or None,
                    "recurso_id": str(request.SenderResourceId or "") or None,
                    "solidset_base_url": str(solidset_instance["BaseUrl"]),
                },
            ))
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

@app.post(
    "/api/v1/agent/solidset/chat-workroom/sync",
    response_model=SysChatIAResourceIngestResponse,
    tags=["SolidSET synchronization"],
    summary="Synchronize resource and workroom assignments from one instance",
)
def sync_solidset_chat_resources(instanceCode: str = Query(...)) -> SysChatIAResourceIngestResponse:
    """Synchronizes resource-to-workroom assignments for the selected instance."""
    try:
        instance = get_solidset_instance(code=instanceCode, source_ip=None)
        if not instance or not instance.get("DataAPI"):
            raise HTTPException(status_code=404, detail="A instância ou a SolidSET Data API não existe.")
        result = ingest_solidset_chat_resources(instance)
    except (pymssql.Error, psycopg.Error, RuntimeError) as exc:
        print(f"❌ No se pudo sincronizar SysChatIAResource: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível sincronizar as relações de chat.",
        ) from exc
    return SysChatIAResourceIngestResponse(status="synchronized", **result)

@app.post(
    "/api/v1/agent/solidset/resources/sync",
    response_model=SysResourceIAIngestResponse,
    tags=["SolidSET synchronization"],
    summary="Synchronize resources from one SolidSET instance",
)
def sync_solidset_resources(instanceCode: str = Query(...)) -> SysResourceIAIngestResponse:
    """Synchronizes SysResources using the SQL Server connection selected by instanceCode."""
    try:
        instance = get_solidset_instance(code=instanceCode, source_ip=None)
        if not instance or not instance.get("DataAPI"):
            raise HTTPException(status_code=404, detail="A instância ou a SolidSET Data API não existe.")
        result = ingest_solidset_resources(instance)
    except (pymssql.Error, psycopg.Error, RuntimeError) as exc:
        print(f"❌ No se pudo sincronizar SysResourceIA: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível sincronizar os recursos entre o SQL Server e o PostgreSQL.",
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
            detail="Não foi possível guardar a configuração no PostgreSQL.",
        ) from exc

    return SysResourceIAConfigurationResponse(
        status="saved",
        configuration=SysResourceIAConfigurationStored(**saved),
    )

@app.post(
    "/api/v1/agent/solidset/instances",
    response_model=SolidSETInstanceConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["SolidSET instances"],
    summary="Register or update a SolidSET instance and its Data API",
)
def register_solidset_instance(
    configuration: SolidSETInstanceConfiguration,
) -> SolidSETInstanceConfigurationResponse:
    """Registers instance routing, regional settings, and its independent Data API."""
    payload = configuration.model_dump()
    for field in ("BaseUrl", "NotificationUrl"):
        value = str(payload.get(field) or "").strip().rstrip("/")
        if field == "BaseUrl" or value:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{field} deve ser um URL HTTP(S) absoluto.",
                )
        payload[field] = value or None
    payload["Code"] = payload["Code"].strip()
    payload["Name"] = payload["Name"].strip()
    payload["SourceIP"] = str(payload.get("SourceIP") or "").strip() or None
    payload["CountryCode"] = payload["CountryCode"].strip().upper()
    language, *locale_parts = payload["Locale"].strip().split("-")
    payload["Locale"] = "-".join([language.lower(), *[part.upper() for part in locale_parts]])
    payload["TimeZone"] = payload["TimeZone"].strip()
    data_api_payload = payload.get("DataAPI")
    if data_api_payload:
        data_api_url = str(data_api_payload.get("BaseUrl") or "").strip().rstrip("/")
        parsed_data_api = urlparse(data_api_url)
        if parsed_data_api.scheme not in {"http", "https"} or not parsed_data_api.netloc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="DataAPI.BaseUrl deve ser um URL HTTP(S) absoluto.",
            )
        data_api_payload["BaseUrl"] = data_api_url
    try:
        ZoneInfo(payload["TimeZone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="TimeZone deve ser um identificador IANA válido, por exemplo Europe/Lisbon.",
        ) from exc
    try:
        saved = save_solidset_instance(payload)
        operation = str(saved.get("_operation", "saved"))
        with _solidset_instance_cache_lock:
            _solidset_instance_cache.clear()
        saved = get_solidset_instance(code=payload["Code"], source_ip=None) or saved
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="SourceIP já está atribuído a outra instância SolidSET.",
        ) from exc
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível guardar a instância SolidSET no PostgreSQL.",
        ) from exc
    saved.pop("_operation", None)
    data_api = saved.get("DataAPI")
    if data_api:
        saved["DataAPI"] = {
            key: data_api.get(key) for key in (
                "BaseUrl", "TimeoutSeconds", "MaxRows", "VerifyTLS", "active",
            )
        }
        saved["DataAPI"]["APIKeyConfigured"] = bool(data_api.get("EncryptedAPIKey"))
    return SolidSETInstanceConfigurationResponse(
        status=operation,
        configuration=SolidSETInstanceStored(**saved),
    )


@app.post(
    "/api/v1/agent/solidset/instances/{code}/test-connection",
    response_model=SolidSETDataAPIConnectionTestResponse,
    tags=["SolidSET instances"],
    summary="Test the configured SolidSET data provider",
)
def test_solidset_instance_database(code: str) -> SolidSETDataAPIConnectionTestResponse:
    """Tests connectivity and basic schema capabilities without exposing credentials."""
    instance = get_solidset_instance(code=code.strip(), source_ip=None)
    if not instance:
        raise HTTPException(status_code=404, detail="A instância SolidSET não existe ou está inativa.")
    if not instance.get("DataAPI"):
        raise HTTPException(status_code=409, detail="A instância não tem um fornecedor de dados configurado.")
    try:
        result = test_solidset_sql_connection(instance)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível estabelecer ligação ao fornecedor de dados desta instância.",
        ) from exc
    return SolidSETDataAPIConnectionTestResponse(
        status="connected", instanceCode=str(instance["Code"]), **result,
    )


@app.put(
    "/api/v1/agent/llm/providers/{code}",
    response_model=LLMProviderConfigurationResponse,
)
def save_llm_provider(
    code: str,
    configuration: LLMProviderConfiguration,
) -> LLMProviderConfigurationResponse:
    """Registra/actualiza un proveedor global o específico de un agente."""
    payload = configuration.model_dump()
    if code.strip().lower() != payload["Code"].strip().lower():
        raise HTTPException(status_code=422, detail="O Code da rota e do corpo devem coincidir.")
    provider = payload["Provider"].strip().lower().replace("-", "_")
    if provider not in ProviderRegistry.names():
        raise HTTPException(status_code=422, detail={
            "message": "Fornecedor LLM não suportado.",
            "available": list(ProviderRegistry.names()),
        })
    payload["Code"] = payload["Code"].strip()
    payload["Name"] = payload["Name"].strip()
    payload["Provider"] = provider
    payload["Model"] = payload["Model"].strip()
    for field in ("BaseUrl", "AzureEndpoint"):
        value = str(payload.get(field) or "").strip().rstrip("/")
        if value:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(status_code=422, detail=f"{field} deve ser um URL HTTP(S) absoluto.")
        payload[field] = value or None
    if provider == "ollama" and not payload.get("BaseUrl"):
        payload["BaseUrl"] = settings.OLLAMA_BASE_URL.rstrip("/")
    if provider in {"openai_compatible", "local_openai"} and not payload.get("BaseUrl"):
        raise HTTPException(status_code=422, detail="BaseUrl é obrigatório para um fornecedor compatível com OpenAI.")
    if provider == "azure_openai" and not (payload.get("AzureEndpoint") or payload.get("BaseUrl")):
        raise HTTPException(status_code=422, detail="AzureEndpoint ou BaseUrl é obrigatório para Azure OpenAI.")
    if payload.get("IDResource") is not None:
        payload["IsDefault"] = False

    # Construir el adaptador detecta dependencias o parámetros incompatibles antes de persistir.
    try:
        create_chat_model(LLMProviderConfig(
            provider=provider, model=payload["Model"], base_url=payload.get("BaseUrl") or "",
            api_key=payload.get("APIKey") or "", temperature=payload["Temperature"],
            max_output_tokens=payload["MaxOutputTokens"], timeout_seconds=payload["TimeoutSeconds"],
            azure_endpoint=payload.get("AzureEndpoint") or "",
            azure_api_version=payload.get("AzureApiVersion") or "",
            azure_deployment=payload.get("AzureDeployment") or "",
        ))
        saved = save_llm_provider_configuration(payload)
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(status_code=404, detail="O IDResource indicado não existe.") from exc
    except (ValueError, RuntimeError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="A configuração do fornecedor não é válida.") from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível guardar o fornecedor no PostgreSQL.") from exc
    agent.clear_llm_configuration_cache()
    return LLMProviderConfigurationResponse(
        status="saved", configuration=LLMProviderConfigurationStored(**saved)
    )


@app.get(
    "/api/v1/agent/llm/providers",
    response_model=list[LLMProviderConfigurationStored],
)
def get_llm_providers() -> list[LLMProviderConfigurationStored]:
    """Lista configuraciones sin exponer sus claves API."""
    try:
        return [LLMProviderConfigurationStored(**row) for row in list_llm_provider_configurations()]
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível consultar os fornecedores.") from exc


@app.delete("/api/v1/agent/llm/providers/{code}")
def deactivate_llm_provider(code: str) -> dict[str, str]:
    """Desactiva una configuración conservando su historial."""
    try:
        changed = deactivate_llm_provider_configuration(code.strip())
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível desativar o fornecedor.") from exc
    if not changed:
        raise HTTPException(status_code=404, detail="A configuração não existe.")
    agent.clear_llm_configuration_cache()
    return {"status": "deactivated", "code": code.strip()}


@app.put(
    "/api/v1/agent/solidset/agents/{agent_resource_id}/model",
    response_model=AgentIAModelStored,
)
def configure_agent_model(
    agent_resource_id: uuid.UUID,
    configuration: AgentIAModelConfiguration,
) -> AgentIAModelStored:
    """Asigna a un agente el modelo y su política de mejora continua."""
    payload = configuration.model_dump()
    payload["ProviderCode"] = payload["ProviderCode"].strip()
    payload["Role"] = payload["Role"].strip()
    try:
        saved = save_agent_model_configuration(agent_resource_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="A configuração solicitada não foi encontrada.") from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(status_code=404, detail="O agente indicado não existe.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A configuração do modelo não é válida.") from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível atribuir o modelo ao agente.") from exc
    agent.clear_llm_configuration_cache()
    return AgentIAModelStored(**saved)


@app.get(
    "/api/v1/agent/solidset/agents/{agent_resource_id}/model",
)
def read_agent_model(agent_resource_id: uuid.UUID) -> dict[str, Any]:
    """Consulta todos los modelos que el router puede usar para el agente."""
    try:
        saved = get_agent_model_configurations(agent_resource_id)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível consultar o modelo do agente.") from exc
    if not saved:
        raise HTTPException(status_code=404, detail="O agente não tem nenhum SysAgentIAModel ativo.")
    return {"IDResource": agent_resource_id, "models": saved}


@app.post(
    "/api/v1/agent/notification/framework-message",
    response_model=SendMessageResultDTO,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_framework_notification(message: FrameworkMessageDTO, request: Request):
    print(message.model_dump_json(indent=2))

    """Recibe desde Notification un FrameworkMessage ya capturado y lo aprende en Qdrant."""

    payload = (
        message.model_dump(mode="json")
        if hasattr(message, "model_dump")
        else message.dict()
    )
    try:
        instance = _resolve_request_solidset_instance(request)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível determinar a instância SolidSET.") from exc
    if instance is None:
        raise HTTPException(
            status_code=400,
            detail="Instância SolidSET desconhecida. Envie X-SolidSET-Instance ou registe o endereço IP de origem.",
        )
    payload["_SolidSETInstanceID"] = str(instance["ID"])
    chat_id = _framework_message_chat_id(payload, [])
    # IDChat2 es la referencia compartida con WPF/SolidSET. Solo se genera un
    # UUID defensivo para notificaciones técnicas que no contienen chat.
    request_id = chat_id or str(uuid.uuid4())
    _create_response_status(request_id, chat_id, 0)
    try:
        if settings.AGENT_RESPONSE_QUEUE_ENABLED:
            await asyncio.to_thread(
                _enqueue_auto_replies,
                payload,
                dict(instance),
                request_id,
                chat_id,
            )
        else:
            capture = notification_listener.capture_realtime_payload(payload)
            candidates = capture.get("auto_reply_candidates") or []
            _attach_solidset_instance(candidates, instance)
            _schedule_auto_replies(candidates, request_id)
    except redis.RedisError as exc:
        _update_response_status(request_id, "failed", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Não foi possível colocar o pedido na fila nem registá-lo para auditoria.",
                "requestId": request_id,
                "error": str(exc),
            },
        ) from exc
    try:
        await asyncio.to_thread(
            save_agent_response_audit,
            request_id,
            chat_id,
            "queued",
            0,
            None,
            payload,
            None,
        )
    except psycopg.Error as exc:
        # Redis Stream ya aceptó el trabajo. No se devuelve 503 porque el
        # cliente podría duplicarlo; el worker reintentará el UPSERT terminal.
        print(f"⚠️ Solicitud encolada sin auditoría inicial PostgreSQL: {exc}")
    print(f"📥 FrameworkMessage encolado requestId={request_id}")
    return SendMessageResultDTO(
        Result=0,
        Message=message,
        Error=None,
        requestId=request_id,
        status="queued",
        statusUrl=f"/api/v1/agent/responses/{request_id}/status",
    )


def _chat_question_suggestion_context(payload: dict[str, Any]) -> dict[str, str]:
    """Extracts the requester and quoted-message identities without mixing them."""
    chat = _get_payload_value(payload, "Chat", "chat")
    chat = chat if isinstance(chat, dict) else {}
    sender = _get_payload_value(payload, "Sender", "sender")
    sender = sender if isinstance(sender, dict) else {}
    destiny = _get_payload_value(payload, "Destiny", "destiny")
    destiny = destiny if isinstance(destiny, dict) else {}
    info = _get_payload_value(payload, "Info", "info")
    info = info if isinstance(info, dict) else {}
    workroom_data = _get_payload_value(payload, "WorkRoomData", "workRoomData")
    workroom_data = workroom_data if isinstance(workroom_data, dict) else {}
    quoted = _get_payload_value(chat, "chatQuestion", "ChatQuestion")
    quoted = quoted if isinstance(quoted, dict) else {}

    current_chat_id = str(
        _get_payload_value(chat, "idChat2", "IDChat2", "idChat") or ""
    ).strip()
    quoted_chat_id = str(
        _get_payload_value(quoted, "idChat2", "IDChat2")
        or _get_payload_value(chat, "chatQuestionMessage", "ChatQuestionMessage")
        or ""
    ).strip()
    requester_resource = _valid_framework_identifier(
        _get_payload_value(chat, "idSenderResource", "IDSenderResource")
    ) or _valid_framework_identifier(
        _get_payload_value(sender, "resource", "IDResource")
    )
    requester_login = _valid_framework_identifier(
        _get_payload_value(chat, "idSender", "IDSender")
    ) or _valid_framework_identifier(_get_payload_value(sender, "login", "IDLogin"))
    quoted_resource = _valid_framework_identifier(
        _get_payload_value(quoted, "idSenderResource", "IDSenderResource")
    )
    quoted_login = _valid_framework_identifier(
        _get_payload_value(quoted, "idSender", "IDSender")
    )
    workroom_id = _valid_framework_identifier(
        _get_payload_value(chat, "idWorkRoom", "IDWorkRoom")
    ) or _valid_framework_identifier(
        _get_payload_value(destiny, "workRoom", "IDWorkRoom")
    ) or _valid_framework_identifier(_get_payload_value(workroom_data, "id", "ID"))
    meeting_id = _valid_framework_identifier(
        _get_payload_value(quoted, "idMeeting", "IDMeeting")
    ) or _valid_framework_identifier(
        _get_payload_value(chat, "idMeeting", "IDMeeting")
    ) or _valid_framework_identifier(_get_payload_value(info, "meeting_id", "meetingId"))
    quoted_message = str(
        _get_payload_value(quoted, "rawMessage", "RawMessage") or ""
    ).strip()
    session_id = _valid_framework_identifier(
        _get_payload_value(sender, "session", "IDSession")
    )
    return {
        "request_id": current_chat_id,
        "quoted_chat_id": quoted_chat_id,
        "quoted_message": quoted_message,
        "requester_resource": requester_resource or "",
        "requester_login": requester_login or "",
        "quoted_resource": quoted_resource or "",
        "quoted_login": quoted_login or "",
        "workroom_id": workroom_id or "",
        "meeting_id": meeting_id or "",
        "meeting_code": str(
            _get_payload_value(info, "meeting_code", "meetingCode") or ""
        ).strip(),
        "session_id": session_id or "",
    }


def _parse_chat_question_suggestions(raw_response: Any, limit: int = 3) -> list[str]:
    """Normalizes model output into distinct, user-selectable suggestions."""
    text = str(raw_response or "").strip()
    if not text:
        return []
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    values: list[Any] = []
    try:
        decoded = json.loads(text)
        if isinstance(decoded, list):
            values = decoded
        elif isinstance(decoded, dict):
            values = decoded.get("suggestions") or decoded.get("sugestoes") or []
    except json.JSONDecodeError:
        values = re.split(r"\n\s*(?:---SUGGESTION---|\d+[.)]\s+)", text)

    suggestions: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("text") or value.get("response") or value.get("suggestion")
        candidate = str(value or "").strip().strip('"')
        normalized = re.sub(r"\s+", " ", candidate).casefold()
        if not candidate or normalized in seen:
            continue
        seen.add(normalized)
        suggestions.append(candidate)
        if len(suggestions) >= limit:
            break
    return suggestions


@app.post(
    "/api/v1/agent/notification/chat-question/suggest-response",
    response_model=ChatQuestionSuggestionResponse,
    summary="Suggest a response to a quoted SolidSET chat message",
    responses={
        200: {
            "description": "Independent response suggestions for user selection.",
        },
        404: {"description": "The requester's own AI agent is not active."},
        422: {"description": "The FrameworkMessage lacks required chat context."},
        503: {"description": "A database or model dependency is unavailable."},
    },
)
async def suggest_chat_question_response(
    message: FrameworkMessageDTO,
    request: Request,
) -> ChatQuestionSuggestionResponse:
    """Returns suggestions grounded in the requester's agent without sending them."""
    payload = message.model_dump(mode="json")
    context = _chat_question_suggestion_context(payload)
    chat_payload = _get_payload_value(payload, "Chat", "chat")
    chat_payload = chat_payload if isinstance(chat_payload, dict) else {}
    current_message = str(
        _get_payload_value(chat_payload, "rawMessage", "RawMessage") or ""
    ).strip()
    request_id = context["request_id"]
    if not request_id:
        raise HTTPException(
            status_code=422,
            detail="O campo Chat.IDChat2 é obrigatório para acompanhar o estado do pedido.",
        )
    if not context["quoted_chat_id"] or not context["quoted_message"]:
        raise HTTPException(
            status_code=422,
            detail="Chat.chatQuestion deve conter IDChat2 e RawMessage.",
        )
    if current_message:
        raise HTTPException(
            status_code=422,
            detail=(
                "Chat.RawMessage deve estar vazio para solicitar sugestões; "
                "o texto a responder deve estar em Chat.chatQuestion.RawMessage."
            ),
        )
    if not context["requester_resource"]:
        raise HTTPException(
            status_code=422,
            detail="Não foi possível identificar o recurso que solicitou a sugestão.",
        )
    if not context["workroom_id"]:
        raise HTTPException(
            status_code=422,
            detail="Não foi possível identificar o canal da conversa.",
        )

    _create_response_status(request_id, request_id, 1)
    status_agent_id = context["requester_resource"]
    agent_name = ""
    try:
        _update_response_status(request_id, "processing")
        solidset_instance = _resolve_request_solidset_instance(request)
        if not solidset_instance or not solidset_instance.get("DataAPI"):
            raise LookupError("A instância SolidSET não tem um fornecedor de dados configurado.")
        verification = await asyncio.to_thread(
            verify_and_sync_solidset_agent_mapping,
            context["requester_resource"],
            None,
            solidset_instance,
        )
        if not verification.get("verified"):
            raise LookupError(
                "O recurso solicitante não possui um agente IA ativo em SysResource2Agent."
            )
        identity = await asyncio.to_thread(
            get_active_agent_identity_for_resource,
            context["requester_resource"],
        )
        if not identity:
            raise LookupError("O agente próprio do recurso não está ativo no PostgreSQL.")
        status_agent_id = str(
            verification.get("IDAgentResource") or identity.get("IDAgentResource") or ""
        ).strip()
        agent_name = _agent_visible_name(identity)
        _update_response_status(
            request_id,
            "searching",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
        )
        private_knowledge = await asyncio.to_thread(
            get_agent_knowledge,
            context["requester_resource"],
            context["workroom_id"],
        )
        reinforcement = await asyncio.to_thread(
            get_agent_reinforcement_context,
            context["requester_resource"],
            context["workroom_id"],
        )
        metadata = {
            "response_suggestion_mode": True,
            "response_suggestion_count": 3,
            "chat_id": request_id,
            "quoted_chat_id": context["quoted_chat_id"],
            "quoted_message": context["quoted_message"],
            "quoted_sender_resource": context["quoted_resource"],
            "quoted_sender_login": context["quoted_login"],
            "requester_resource": context["requester_resource"],
            "requester_login": context["requester_login"],
            "agent_resource_id": context["requester_resource"],
            "agent_identity_id": status_agent_id,
            "agent_name": agent_name,
            "agent_knowledge": private_knowledge,
            "agent_reinforcement": reinforcement,
            "workroom_id": context["workroom_id"],
            "recipient_count": 1,
            "importance": int(message.Importance or 0),
            "country_code": str(_get_payload_value(message.Info, "country_code") or "PT"),
            "locale": str(_get_payload_value(message.Info, "locale") or "pt-PT"),
            "time_zone": str(
                _get_payload_value(message.Info, "time_zone", "timezone")
                or "Europe/Lisbon"
            ),
        }
        _update_response_status(
            request_id,
            "thinking",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
        )
        scoped_session = (
            f"solidset:suggestion:agent:{context['requester_resource']}:"
            f"session:{context['session_id'] or request_id}:"
            f"question:{context['quoted_chat_id']}"
        )
        raw_suggestions = await asyncio.to_thread(
            _invoke_orchestrator_for_instance,
            str(solidset_instance["Code"]),
            session_id=scoped_session,
            user_text=context["quoted_message"],
            user_id=context["requester_resource"],
            canal_id=context["workroom_id"],
            meeting_id=context["meeting_id"] or None,
            meeting_code=context["meeting_code"] or None,
            message_kind=str(message.Kind or "ChatMessage"),
            message_category="chat_question_response_suggestion",
            message_metadata=metadata,
            tool_allowlist=set(),
            auto_reply_mode=True,
        )
        suggestions = _parse_chat_question_suggestions(raw_suggestions, limit=3)
        if not suggestions:
            raise RuntimeError("O modelo não gerou sugestões de resposta.")
        language = agent._detect_user_language(context["quoted_message"])
        result = {
            "questionChatId": context["quoted_chat_id"],
            "language": language,
            "suggestions": [
                {"id": str(index), "text": text}
                for index, text in enumerate(suggestions, start=1)
            ],
        }
        _update_response_status(
            request_id,
            "completed",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
            response_count=len(suggestions),
            result=result,
        )
        return ChatQuestionSuggestionResponse(
            requestId=request_id,
            questionChatId=context["quoted_chat_id"],
            status="completed",
            code=_RESPONSE_STATUS_CODES["completed"],
            language=language,
            suggestions=[
                ChatQuestionSuggestionItem(**item) for item in result["suggestions"]
            ],
            statusUrl=f"/api/v1/agent/responses/{request_id}/status",
        )
    except LookupError as exc:
        _update_response_status(
            request_id,
            "failed",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
            error=str(exc),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, psycopg.Error, pymssql.Error, RuntimeError) as exc:
        _update_response_status(
            request_id,
            "failed",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
            error=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail="Não foi possível gerar a sugestão de resposta.",
        ) from exc
    except Exception as exc:
        _update_response_status(
            request_id,
            "failed",
            agent_resource_id=status_agent_id,
            agent_name=agent_name,
            error=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail="Não foi possível gerar a sugestão de resposta.",
        ) from exc


@app.get("/api/v1/agent/responses/status")
def read_agent_response_status_by_chat(
    chatId: str = Query(...), lang: str = Query("pt", pattern="^(es|en|pt)$")
) -> dict[str, Any]:
    """Devuelve la solicitud más reciente asociada al IDChat2 indicado."""
    data = _load_response_status_by_chat(str(chatId).strip())
    if data is None:
        raise HTTPException(status_code=404, detail="Não existe um estado para esse chatId.")
    return _localize_response_status(data, lang)


@app.get("/api/v1/agent/responses/queue/status")
def read_agent_response_queue_status() -> dict[str, Any]:
    """Métricas operativas del Stream y sus workers."""
    try:
        return response_queue.stats()
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="O Redis Stream não está disponível.") from exc


class HistoricalIngestionStartRequest(BaseModel):
    instanceCode: Optional[str] = None
    dryRun: bool = True


def _require_historical_admin(
    x_agent_admin_key: str = Header(
        ...,
        alias="X-Agent-Admin-Key",
        description="Administrative key configured in HISTORICAL_INGESTION_ADMIN_KEY.",
    ),
) -> None:
    configured = settings.HISTORICAL_INGESTION_ADMIN_KEY.strip()
    if not configured:
        raise HTTPException(status_code=503, detail="Configure HISTORICAL_INGESTION_ADMIN_KEY.")
    if x_agent_admin_key != configured:
        raise HTTPException(status_code=401, detail="Credencial administrativa inválida.")


@app.post(
    "/api/v1/agent/historical-ingestion/start",
    status_code=202,
    dependencies=[Depends(_require_historical_admin)],
)
async def start_historical_ingestion(
    configuration: HistoricalIngestionStartRequest,
) -> dict[str, Any]:
    try:
        instances = (
            [get_solidset_instance(code=configuration.instanceCode, source_ip=None)]
            if configuration.instanceCode else list_active_solidset_instances()
        )
        instances = [instance for instance in instances if instance]
        if not instances:
            raise HTTPException(status_code=404, detail="Não existem instâncias SolidSET ativas.")
        historical_queue.set_paused(False)
        results = [
            await asyncio.to_thread(enqueue_next_batch, instance, configuration.dryRun)
            for instance in instances
        ]
        return {"status":"accepted", "dryRun":configuration.dryRun, "instances":results}
    except (pymssql.Error, psycopg.Error, redis.RedisError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Não foi possível iniciar o lote de ingestão histórica.") from exc


@app.post(
    "/api/v1/agent/historical-ingestion/pause",
    dependencies=[Depends(_require_historical_admin)],
)
def pause_historical_ingestion() -> dict[str, Any]:
    historical_queue.set_paused(True)
    return {"status":"paused"}


@app.post(
    "/api/v1/agent/historical-ingestion/resume",
    dependencies=[Depends(_require_historical_admin)],
)
def resume_historical_ingestion() -> dict[str, Any]:
    historical_queue.set_paused(False)
    return {"status":"running"}


@app.post(
    "/api/v1/agent/historical-ingestion/approve-dry-run",
    dependencies=[Depends(_require_historical_admin)],
)
def approve_historical_dry_run(
    instanceCode: str = Query(...)
) -> dict[str, Any]:
    instance = get_solidset_instance(code=instanceCode, source_ip=None)
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada.")
    approved = approve_dry_run_cursors(str(instance["ID"]))
    return {"status":"approved", "approvedCursors":approved}


@app.get(
    "/api/v1/agent/historical-ingestion/status",
    dependencies=[Depends(_require_historical_admin)],
)
def historical_ingestion_status(
    resourceId: Optional[uuid.UUID] = Query(None),
) -> dict[str, Any]:
    return {
        "queue":historical_queue.stats(),
        "cursors":list_historical_cursors(str(resourceId) if resourceId else None),
    }


@app.get(
    "/api/v1/agent/historical-ingestion/batches",
    dependencies=[Depends(_require_historical_admin)],
)
def historical_ingestion_batches(
    limit: int = Query(50, ge=1, le=500),
    resourceId: Optional[uuid.UUID] = Query(None),
) -> dict[str, Any]:
    return {
        "items":list_historical_audits(limit, str(resourceId) if resourceId else None)
    }


@app.delete(
    "/api/v1/agent/historical-ingestion/messages/{id_chat2}",
    dependencies=[Depends(_require_historical_admin)],
)
def delete_historical_message(
    id_chat2: int,
    instanceCode: str = Query(...),
    sourceType: str = Query("chat", pattern="^(chat|task)$"),
) -> dict[str, Any]:
    instance = get_solidset_instance(code=instanceCode, source_ip=None)
    if not instance: raise HTTPException(status_code=404, detail="Instância não encontrada.")
    points = historical_points(str(instance["ID"]), id_chat2, sourceType)
    if points:
        QdrantClient(url=settings.VECTOR_DB_URL).delete(
            collection_name=settings.VECTOR_COLLECTION_NAME,
            points_selector=PointIdsList(points=points), wait=True,
        )
    deleted = mark_historical_deleted(str(instance["ID"]), id_chat2, sourceType)
    return {
        "status":"deleted", "idChat2":id_chat2,
        "sourceType":sourceType, "documents":deleted,
    }


@app.get("/api/v1/agent/responses/{request_id}/status")
def read_agent_response_status(
    request_id: str, lang: str = Query("pt", pattern="^(es|en|pt)$")
) -> dict[str, Any]:
    """Devuelve el estado de procesamiento de una respuesta automática."""
    data = _load_response_status(request_id.strip())
    if data is None:
        raise HTTPException(status_code=404, detail="O pedido não existe ou expirou.")
    return _localize_response_status(data, lang)


def _inflate_solidset_form_payload(form_payload: dict[str, Any]) -> dict[str, Any]:
    """Convierte las claves de SendMessageForm en el JSON lógico de SolidSET."""
    result: dict[str, Any] = {}
    token_pattern = re.compile(r"([^.\[\]]+)|\[([^\]]+)\]")
    for flat_key, raw_value in form_payload.items():
        tokens: list[str | int] = []
        for match in token_pattern.finditer(str(flat_key)):
            token = match.group(1) if match.group(1) is not None else match.group(2)
            tokens.append(int(token) if str(token).isdigit() else str(token))
        if not tokens:
            continue
        value = raw_value
        if flat_key == "ExtraData" and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        elif isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"

        current: Any = result
        for index, token in enumerate(tokens):
            last = index == len(tokens) - 1
            next_token = None if last else tokens[index + 1]
            if isinstance(token, int):
                while len(current) <= token:
                    current.append(None)
                if last:
                    current[token] = value
                elif current[token] is None:
                    current[token] = [] if isinstance(next_token, int) else {}
                current = current[token]
            else:
                if last:
                    current[token] = value
                else:
                    if token not in current:
                        current[token] = [] if isinstance(next_token, int) else {}
                    current = current[token]
    return result


@app.post("/api/v1/agent/notification/framework-message/preview")
async def preview_framework_notification(
    message: FrameworkMessageDTO, request: Request
) -> dict[str, Any]:
    """Genera la respuesta y devuelve su payload sin enviarlo a SolidSET."""
    payload = message.model_dump(mode="json")
    try:
        instance = _resolve_request_solidset_instance(request)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível determinar a instância SolidSET."
        ) from exc
    if instance is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Instância SolidSET desconhecida. Envie X-SolidSET-Instance "
                "ou registe o endereço IP de origem."
            ),
        )
    payload["_SolidSETInstanceID"] = str(instance["ID"])
    capture = notification_listener.capture_realtime_payload(payload)
    candidates = capture.get("auto_reply_candidates") or []
    _attach_solidset_instance(candidates, instance)
    if capture["errors"]:
        raise HTTPException(
            status_code=503,
            detail=f"Não foi possível processar a mensagem: {capture['errors']} erro(s).",
        )
    flat_payloads = await _process_auto_replies(candidates, preview_only=True)
    logical_payloads = [
        _inflate_solidset_form_payload(item) for item in flat_payloads
    ]
    return {
        "Result": 0,
        "Learned": capture["learned"],
        "Skipped": capture["skipped"],
        "PayloadCount": len(logical_payloads),
        "Payloads": logical_payloads,
    }

@app.post("/api/v1/agent/notification/frameworkHub/SendMessage")
async def capture_and_forward_framework_message(request: Request):
    print(request)
    """Captura el mensaje en Qdrant antes de reenviarlo al endpoint real de SolidSET."""
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {"RawMessage": raw_body.decode("utf-8", errors="replace")}

    try:
        instance = _resolve_request_solidset_instance(request)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível determinar a instância SolidSET.") from exc
    if instance is None:
        raise HTTPException(
            status_code=400,
            detail="Instância SolidSET desconhecida. Envie X-SolidSET-Instance ou registe o endereço IP de origem.",
        )
    if isinstance(payload, dict):
        payload["_SolidSETInstanceID"] = str(instance["ID"])
    capture = notification_listener.capture_realtime_payload(payload)
    candidates = capture.get("auto_reply_candidates") or []
    _attach_solidset_instance(candidates, instance)

    upstream_base = str(instance.get("NotificationUrl") or "").rstrip("/")
    if not upstream_base:
        raise HTTPException(status_code=503, detail={
            "message": "A instância não tem NotificationUrl configurado para reencaminhar a mensagem.",
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
            "message": "A mensagem foi capturada, mas não foi possível reencaminhá-la para o SolidSET.",
            "capture": capture,
        }) from exc

    # Este proxy es con frecuencia la primera entrada del mensaje. Debe programar
    # aquí la respuesta porque la notificación posterior tendrá la misma huella y
    # será correctamente descartada como duplicada. Solo se responde si SolidSET
    # aceptó primero el mensaje original.
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
            detail="RawMessage ou Chat.rawMessage é obrigatório para processar um FrameworkMessage em /dialogue.",
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
                agent_response="Por favor, escreva uma mensagem para que eu possa ajudar."
            )
        
        # Validar largo del mensaje (prevenir abusos)
        if len(req.message) > 5000:
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message[:100] + "...",
                agent_response="A mensagem é demasiado longa. Reduza o pedido para menos de 5000 caracteres."
            )
        
        # Detectar inyección de prompts
        if detect_prompt_injection(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="Não posso processar este pedido devido às políticas de segurança. Reformule o pedido técnico de forma clara e direta."
            )
        
        # Detectar inyección SQL
        if detect_sql_injection(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="Foi detetada uma tentativa de injeção SQL. Apenas posso executar consultas de leitura (SELECT) seguras. Indique os dados que pretende consultar."
            )
        
        # Detectar contenido ofensivo
        if detect_offensive_content(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="Mantenha um tom respeitador na conversa. Estou disponível para ajudar com questões técnicas sobre maquinaria e sistemas."
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
                agent_response="O agente está a processar várias conversas neste momento. Tente novamente dentro de alguns segundos."
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
                agent_response="O pedido está a demorar mais do que o esperado. Tente novamente dentro de alguns segundos."
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
            agent_response="Ocorreu um erro ao processar o pedido. Tente novamente ou contacte o administrador do sistema."
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
            reaction_signal=reaction.get("signal", "sem_sinal"),
            topics=reaction.get("topics", []),
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Não foi possível registar o feedback do utilizador."
        )


@app.post(
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
            raise RuntimeError("A instância SolidSET não tem uma SolidSET Data API configurada.")
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
        changed and signal != "removed"
        and agent_learning_enabled(message["IDAgentResource"], "reactions")
    ):
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
                "reward": reward,
                "agent_resource_id": str(message["IDAgentResource"]),
            },
        )))

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
    raise HTTPException(status_code=404, detail="Ficheiro de áudio não encontrado.")


@app.get("/api/v1/agent/history/{session_id}")
def get_chat_history(
    session_id: str,
    before: int = Query(
        0,
        ge=0,
        description="Number of recent messages to skip before returning results (backward-scroll cursor)."
    ),
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="Maximum number of messages returned per page."
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
            detail="Não foi possível obter o histórico."
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
            "message": "Histórico eliminado com sucesso"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail="Não foi possível eliminar o histórico."
        )


@app.get("/api/v1/agent/health")
def health_check():
    """
    Endpoint de salud para verificar que el servicio está funcionando.
    """
    try:
        db_llm = get_llm_provider_configuration()
    except psycopg.Error:
        db_llm = None
    return {
        "status": "healthy",
        "version": "2.0.0",
        "services": {
            "llm": {
                "source": "postgresql" if db_llm else "environment_fallback",
                "provider": (db_llm or {}).get("Provider", settings.LLM_PROVIDER),
                "model": (db_llm or {}).get("Model", settings.MODEL_NAME),
                "base_url": (db_llm or {}).get("BaseUrl") or settings.LLM_BASE_URL or settings.OLLAMA_BASE_URL,
            },
            "ollama_embeddings": settings.OLLAMA_BASE_URL,
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
            detail="Não foi possível criar o resumo da avaliação."
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
            detail="Não foi possível obter as mensagens recentes do serviço de notificações."
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
                "error": "Utilizador não encontrado ou sem contexto disponível"
            }
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail="Não foi possível obter o contexto do utilizador."
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
            detail="Não foi possível obter as métricas de SQL."
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
            "message": "Estatísticas de novas tentativas de SQL repostas com sucesso",
            "previous": previous,
            "current": current,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Não foi possível repor as métricas de SQL."
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
            "message": "SOLIDSET_RESTAPI_BASE_URL não está configurado no ambiente",
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
        results["message"] = "Comunicação com a API SolidSET estabelecida com sucesso (Heartbeat OK)"
    elif swagger_ok:
        results["overall_status"] = "partial"
        results["message"] = "A API SolidSET está acessível, mas o Heartbeat não responde corretamente"
    elif openapi_ok:
        results["overall_status"] = "partial"
        results["message"] = "O OpenAPI da API SolidSET está acessível, mas os serviços principais não respondem"
    else:
        results["overall_status"] = "unreachable"
        results["message"] = "Não foi possível estabelecer comunicação com a API SolidSET"
    
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
            "error": "SOLIDSET_RESTAPI_BASE_URL não está configurado"
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

def _swagger_tag_for_path(path: str) -> str:
    """Classify each operation into a stable Swagger UI section."""
    if "/historical-ingestion" in path:
        return "Historical Ingestion"
    if "/responses" in path:
        return "Asynchronous Responses"
    if "/notification" in path:
        return "SolidSET Notifications"
    if "/llm/providers" in path:
        return "LLM Providers"
    if "/solidset/agents" in path or "/solidset/multi-agent" in path:
        return "SolidSET Agents"
    if "/solidset/" in path:
        return "SolidSET Configuration"
    if path.endswith("/feedback") or "/reactions/" in path or "/evaluation/" in path:
        return "Learning and Feedback"
    if "/audio-response" in path or "/history/" in path or "/context/" in path:
        return "Audio, History and Context"
    if path.endswith("/dialogue"):
        return "Conversation"
    if "/connectivity/" in path:
        return "Connectivity"
    return "Observability"


for route in app.routes:
    if isinstance(route, APIRoute):
        tag = _swagger_tag_for_path(route.path)
        route.tags = [tag]
        route.summary = route.name.replace("_", " ").title().replace("Solidset", "SolidSET")
        route.description = next(
            item["description"] for item in OPENAPI_TAGS if item["name"] == tag
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
