from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.connectors.db_client import get_llm_provider_configuration
from app.services.connectivity import (
    _extract_host_port_from_url,
    _probe_http,
    _probe_tcp,
    _run_startup_connectivity_checks,
)
from app.services.dialogue_runtime import active_count as _get_active_dialogues
from app.services.dialogue_runtime import (
    metrics_snapshot as _get_dialogue_metrics_snapshot,
)


router = APIRouter()
app = None
agent = None
notification_listener = None


def configure(
    runtime_app: Any, runtime_agent: Any, runtime_notification_listener: Any
) -> None:
    global app, agent, notification_listener
    app = runtime_app
    agent = runtime_agent
    notification_listener = runtime_notification_listener


@router.get("/api/v1/agent/health", tags=["Observability"])
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
                "base_url": (db_llm or {}).get("BaseUrl")
                or settings.LLM_BASE_URL
                or settings.OLLAMA_BASE_URL,
            },
            "ollama_embeddings": settings.EMBEDDING_BASE_URL,
            "qdrant": settings.VECTOR_DB_URL,
            "redis": settings.REDIS_URL,
        },
        "runtime": {
            "active_dialogues": _get_active_dialogues(),
            "dialogue_max_concurrent": settings.DIALOGUE_MAX_CONCURRENT,
            "dialogue_admission_timeout_seconds": settings.DIALOGUE_ADMISSION_TIMEOUT_SECONDS,
            "dialogue_processing_timeout_seconds": settings.DIALOGUE_PROCESSING_TIMEOUT_SECONDS,
            "dialogue_hard_timeout_seconds": settings.DIALOGUE_HARD_TIMEOUT_SECONDS,
            "dialogue_slow_log_seconds": settings.DIALOGUE_SLOW_LOG_SECONDS,
            "compact_runtime_prompt": settings.LLM_COMPACT_RUNTIME_PROMPT,
            "dialogue_max_output_tokens": settings.LLM_DIALOGUE_MAX_OUTPUT_TOKENS,
            "suggestion_max_output_tokens": settings.LLM_SUGGESTION_MAX_OUTPUT_TOKENS,
            "interactive_priority_enabled": settings.INTERACTIVE_PRIORITY_ENABLED,
            "ingestion_pause_during_interactive": settings.INGESTION_PAUSE_DURING_INTERACTIVE,
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
            "last_notification_poll_at": getattr(
                app.state, "last_notification_poll_at", None
            ),
            "last_notification_result": getattr(
                app.state, "last_notification_result", None
            ),
            "last_notification_error": getattr(
                app.state, "last_notification_error", None
            ),
            "notification_warmup": getattr(app.state, "notification_warmup", None),
            "notification_api_metrics": notification_listener.get_api_metrics_snapshot(),
            "notification_learning_metrics": notification_listener.get_learning_metrics_snapshot(),
            "startup_connectivity": getattr(app.state, "startup_connectivity", None),
            "db_study_interval_seconds": settings.DB_STUDY_INTERVAL_SECONDS,
            "db_study_idle_check_seconds": settings.DB_STUDY_IDLE_CHECK_SECONDS,
            "db_study_max_run_seconds": settings.DB_STUDY_MAX_RUN_SECONDS,
            "last_db_study_at": getattr(app.state, "last_db_study_at", None),
            "last_db_study_error": getattr(app.state, "last_db_study_error", None),
        },
    }


@router.get(
    "/api/v1/agent/evaluation/summary",
    tags=["Learning and Feedback"],
)
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
                "last_notification_error": getattr(
                    app.state, "last_notification_error", None
                ),
            },
            "metricas_evolucion": {
                "learning_runtime": notification_listener.get_learning_metrics_snapshot(),
                "last_notification_result": getattr(
                    app.state, "last_notification_result", None
                ),
                "last_db_study_at": getattr(app.state, "last_db_study_at", None),
                "last_db_study_error": getattr(app.state, "last_db_study_error", None),
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Não foi possível criar o resumo da avaliação."
        )


@router.get(
    "/api/v1/agent/notification/recent-messages",
    tags=["SolidSET Notifications"],
)
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
            detail="Não foi possível obter as mensagens recentes do serviço de notificações.",
        )


@router.get(
    "/api/v1/agent/context/{user_id}",
    tags=["Audio, History and Context"],
)
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
                "recursos_count": len(contexto.recursos_disponibles),
            }
        else:
            return {
                "user_id": user_id,
                "dynamic_profile": perfil_dinamico,
                "error": "Utilizador não encontrado ou sem contexto disponível",
            }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Não foi possível obter o contexto do utilizador."
        )


@router.get("/api/v1/agent/sql-retry-stats", tags=["Observability"])
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
            status_code=500, detail="Não foi possível obter as métricas de SQL."
        )


@router.post("/api/v1/agent/sql-retry-stats/reset", tags=["Observability"])
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
            status_code=500, detail="Não foi possível repor as métricas de SQL."
        )


@router.get("/api/v1/connectivity/solidset", tags=["Connectivity"])
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
            "configured": False,
        }

    results = {
        "status": "ok",
        "base_url": base_url,
        "timestamp": datetime.utcnow().isoformat(),
        "tests": {},
    }

    # Test 1: Heartbeat (END-POINT PRINCIPAL)
    heartbeat_result = _probe_http(base_url, "/RestApi/Heartbeat")
    results["tests"]["heartbeat"] = {
        "endpoint": f"{base_url}/RestApi/Heartbeat",
        "success": heartbeat_result.get("ok", False),
        "status_code": heartbeat_result.get("status_code"),
        "error": heartbeat_result.get("error"),
    }

    # Test 2: Swagger (para verificar que la API está servida)
    swagger_result = _probe_http(base_url, "User/LoginJson")
    results["tests"]["swagger"] = {
        "endpoint": f"{base_url}User/LoginJson",
        "success": swagger_result.get("ok", False),
        "status_code": swagger_result.get("status_code"),
        "error": swagger_result.get("error"),
    }

    # Test 3: OpenAPI spec (algunas instalaciones lo exponen en /openapi.json)
    openapi_result = _probe_http(base_url, "/openapi.json")
    results["tests"]["openapi"] = {
        "endpoint": f"{base_url}/openapi.json",
        "success": openapi_result.get("ok", False),
        "status_code": openapi_result.get("status_code"),
        "error": openapi_result.get("error"),
    }

    # Determinar estado general
    heartbeat_ok = bool(results["tests"].get("heartbeat", {}).get("success", False))
    swagger_ok = bool(results["tests"].get("swagger", {}).get("success", False))
    openapi_ok = bool(results["tests"].get("openapi", {}).get("success", False))

    if heartbeat_ok:
        results["overall_status"] = "healthy"
        results["message"] = (
            "Comunicação com a API SolidSET estabelecida com sucesso (Heartbeat OK)"
        )
    elif swagger_ok:
        results["overall_status"] = "partial"
        results["message"] = (
            "A API SolidSET está acessível, mas o Heartbeat não responde corretamente"
        )
    elif openapi_ok:
        results["overall_status"] = "partial"
        results["message"] = (
            "O OpenAPI da API SolidSET está acessível, mas os serviços principais não respondem"
        )
    else:
        results["overall_status"] = "unreachable"
        results["message"] = (
            "Não foi possível estabelecer comunicação com a API SolidSET"
        )

    return results


@router.get("/api/v1/connectivity/all", tags=["Connectivity"])
def test_all_connectivity():
    """
    Prueba de conectividad con todos los servicios externos configurados.
    """
    base_url = settings.SOLIDSET_RESTAPI_BASE_URL

    results = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {},
    }

    # Test SolidSET
    if base_url:
        heartbeat_result = _probe_http(base_url, "/RestApi/Heartbeat")
        results["services"]["solidset_restapi"] = {
            "configured": True,
            "base_url": base_url,
            "heartbeat_ok": heartbeat_result.get("ok", False),
            "status_code": heartbeat_result.get("status_code"),
            "error": heartbeat_result.get("error"),
        }
    else:
        results["services"]["solidset_restapi"] = {
            "configured": False,
            "error": "SOLIDSET_RESTAPI_BASE_URL não está configurado",
        }

    # Test the isolated interactive and embedding runtimes independently.
    ollama_result = _probe_http(settings.OLLAMA_BASE_URL, "/api/tags")
    results["services"]["ollama_chat"] = {
        "url": settings.OLLAMA_BASE_URL,
        "ok": ollama_result.get("ok", False),
        "status_code": ollama_result.get("status_code"),
        "error": ollama_result.get("error"),
    }
    embedding_result = _probe_http(settings.EMBEDDING_BASE_URL, "/api/tags")
    results["services"]["ollama_embeddings"] = {
        "url": settings.EMBEDDING_BASE_URL,
        "ok": embedding_result.get("ok", False),
        "status_code": embedding_result.get("status_code"),
        "error": embedding_result.get("error"),
    }

    # Test Qdrant
    qdrant_result = _probe_http(settings.VECTOR_DB_URL, "/collections")
    results["services"]["qdrant"] = {
        "url": settings.VECTOR_DB_URL,
        "ok": qdrant_result.get("ok", False),
        "status_code": qdrant_result.get("status_code"),
        "error": qdrant_result.get("error"),
    }

    # Test Redis (TCP)
    redis_host, redis_port = _extract_host_port_from_url(settings.REDIS_URL, 6379)
    redis_result = _probe_tcp(redis_host, redis_port)
    results["services"]["redis"] = {
        "url": settings.REDIS_URL,
        "ok": redis_result.get("ok", False),
        "error": redis_result.get("error"),
    }

    return results
