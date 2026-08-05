import asyncio
import json
import os
import re
import socket
import ssl
import threading
import httpx
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
from app.agent.speech import text_to_speech
from app.agent.tools import solidset_send_chat_message
from app.system.ingest import ingestar_sistema_completo
from app.system.notification_listener import NotificationApiListener

# ============================================================
# CONFIGURACIÓN DE LA APLICACIÓN
# ============================================================

app = FastAPI(
    title="Machining Assistant Agent API",
    description="Agente inteligente para diagnóstico de maquinaria CNC con sistema de aprendizaje contextual",
    version="2.0.0"
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
notification_listener = NotificationApiListener()

_active_dialogues = 0
_active_dialogues_lock = threading.Lock()
_dialogue_slots = threading.BoundedSemaphore(value=max(1, settings.DIALOGUE_MAX_CONCURRENT))
_dialogue_cache_lock = threading.Lock()
_dialogue_response_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()

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


def _is_self_sender(sender_resource: str, sender_name: str) -> bool:
    own_resource = (settings.SOLIDSET_LOGIN_RESOURCE_ID or "").strip().lower()
    own_username = (settings.SOLIDSET_LOGIN_USERNAME or "").strip().lower()
    sender_resource_norm = (sender_resource or "").strip().lower()
    sender_name_norm = (sender_name or "").strip().lower()

    if own_resource and sender_resource_norm and own_resource == sender_resource_norm:
        return True
    if own_username and sender_name_norm and own_username == sender_name_norm:
        return True
    return False


def _sanitize_auto_reply_input(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""
    mention_token = (settings.SOLIDSET_AUTO_REPLY_MENTION_TOKEN or "").strip()
    if mention_token:
        text = text.replace(mention_token, " ")
        text = text.replace(mention_token.lower(), " ")
        text = text.replace(mention_token.upper(), " ")
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


def _candidate_qualifies_for_auto_reply(candidate: dict) -> bool:
    fingerprint = (candidate.get("fingerprint") or "").strip()
    if not fingerprint or _auto_reply_seen(fingerprint):
        return False

    channel_id = (candidate.get("channel_id") or "").strip()
    message = (candidate.get("message") or "").strip()
    sender_resource = str(candidate.get("sender_resource") or "")
    sender_name = str(candidate.get("sender_name") or "")
    if not channel_id or not message:
        return False
    if not _looks_like_question_or_request(message):
        return False
    if (not settings.SOLIDSET_AUTO_REPLY_ALLOW_SELF) and _is_self_sender(sender_resource=sender_resource, sender_name=sender_name):
        return False

    if settings.SOLIDSET_AUTO_REPLY_REQUIRE_MENTION and not candidate.get("addressed_to_agent"):
        token = (settings.SOLIDSET_AUTO_REPLY_MENTION_TOKEN or "").strip().lower()
        if token and token not in message.lower():
            return False

    return True


async def _process_auto_replies(candidates: list[dict]) -> int:
    if not settings.SOLIDSET_AUTO_REPLY_ENABLED:
        return 0
    if not settings.SOLIDSET_USER_ACTIONS_ENABLED:
        print("⚠️ Auto-reply SOLIDSET activo en config, pero SOLIDSET_USER_ACTIONS_ENABLED=false. No se enviarán respuestas.")
        return 0

    max_replies = max(1, settings.SOLIDSET_AUTO_REPLY_MAX_PER_CYCLE)
    sent = 0
    local_seen = set()

    for candidate in candidates:
        if sent >= max_replies:
            break
        fingerprint = (candidate.get("fingerprint") or "").strip()
        if not fingerprint or fingerprint in local_seen:
            continue
        local_seen.add(fingerprint)

        if not _candidate_qualifies_for_auto_reply(candidate):
            continue

        incoming_text = _sanitize_auto_reply_input(str(candidate.get("message") or ""))
        channel_id = (candidate.get("channel_id") or "").strip()
        if not incoming_text or not channel_id:
            continue

        session_id = f"solidset_auto_{channel_id[:8]}"
        user_id = str(
            candidate.get("sender_resource")
            or candidate.get("sender_name")
            or settings.SOLIDSET_LOGIN_USERNAME
            or "solidset.agent"
        ).strip()

        try:
            response_text = await asyncio.to_thread(
                agent.analyze_event_with_dialogue,
                session_id=session_id,
                user_text=incoming_text,
                user_id=user_id,
                canal_id=channel_id,
            )
        except Exception as exc:
            print(f"⚠️ Error generando auto-respuesta para canal {channel_id}: {exc}")
            continue

        response_text = (response_text or "").strip()
        if not response_text:
            continue

        try:
            send_result = await asyncio.to_thread(
                solidset_send_chat_message.invoke,
                {
                    "canal_id": channel_id,
                    "mensaje": response_text,
                    "confirm": True,
                },
            )
            send_result_text = str(send_result)
            if send_result_text.startswith("✅"):
                sent += 1
                _remember_auto_reply_fingerprint(fingerprint)
                print(
                    f"🤖 Auto-reply enviado channel={channel_id} "
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
    Importance: int = 0
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
    "/api/v1/agent/notification/framework-message",
    response_model=SendMessageResultDTO,
)
async def receive_framework_notification(message: FrameworkMessageDTO):
    """Recibe desde Notification un FrameworkMessage ya capturado y lo aprende en Qdrant."""

    print(f"📩 Recibido FrameworkMessage para indexación en Qdrant: {message}")

    payload = (
        message.model_dump(mode="json")
        if hasattr(message, "model_dump")
        else message.dict()
    )
    capture = notification_listener.capture_realtime_payload(payload)
    candidates = capture.get("auto_reply_candidates") or []
    if candidates:
        asyncio.create_task(_process_auto_replies(candidates))
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

    response_headers = {}
    if upstream.headers.get("content-type"):
        response_headers["content-type"] = upstream.headers["content-type"]
    response_headers["X-Agent-Capture-Learned"] = str(capture["learned"])
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )

@app.post("/api/v1/agent/dialogue", response_model=ChatConversationResponse)
def handle_dialogue(req: ChatConversationRequest):
    """
    Procesa un diálogo con el agente.
    
    - Valida la seguridad del mensaje
    - Obtiene el contexto del usuario (sistema de aprendizaje)
    - Procesa la consulta con el agente
    - Genera audio si se solicita
    """
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
                response_holder["text"] = agent.analyze_event_with_dialogue(
                    session_id=req.session_id,
                    user_text=req.message,
                    user_id=req.user_id,
                    canal_id=effective_canal_id,
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
        le=50,
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
