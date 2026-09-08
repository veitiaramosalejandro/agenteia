import asyncio
import psycopg
from contextlib import suppress
from datetime import datetime
from time import perf_counter
from typing import Any
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.interactive_priority import interactive_work

from app.api.controllers.agent_prompts import router as agent_prompts_router
from app.api.controllers.ingestion import router as ingestion_router
from app.api.controllers.synchronization import router as synchronization_router
from app.api.controllers.llm_configuration import router as llm_configuration_router
from app.api.controllers.responses import router as responses_router
from app.api.controllers import diagnostics
from app.api.controllers.history import router as history_router
from app.api.controllers import feedback
from app.api.controllers import conversation
from app.api.controllers import notifications
from app.api.controllers import suggestions as suggestions_controller
from app.api.controllers import agent_management
from app.api.controllers.solidset_instances import router as solidset_instances_router
from app.api.openapi import configure_openapi
from app.connectors.db_client import (
    ensure_llm_provider_schema,
    ensure_agent_model_schema,
    ensure_agent_response_audit_schema,
    ensure_agent_tool_audit_schema,
    ensure_solidset_agent_resource_schema,
    quarantine_legacy_generated_knowledge,
    list_active_solidset_instances,
)
from app.system.ingest import ingestar_sistema_completo
from app.connectors.solidset_sql import (
    instance_context as solidset_sql_instance_context,
)
from app.services import dialogue_runtime
from app.services.connectivity import (
    _log_startup_connectivity,
    _run_startup_connectivity_checks,
)
from app.services.instance_resolution import (
    _request_ip_details,
)
from app.services.dialogue_runtime import (
    active_count as _get_active_dialogues,
)
from app.historical.store import (
    ensure_schema as ensure_historical_schema,
)

from app.services.auto_reply import (
    _process_auto_replies,
)
from app.container import (
    agent,
    notification_listener,
    orchestrator,
    response_queue,
    suggestion_queue,
)


# ============================================================
# CONFIGURACIÓN DE LA APLICACIÓN
# ============================================================

OPENAPI_TAGS = [
    {
        "name": "Conversation",
        "description": "Direct requests and conversational agent execution.",
    },
    {
        "name": "SolidSET Notifications",
        "description": "FrameworkMessage reception, preview, and capture.",
    },
    {
        "name": "Asynchronous Responses",
        "description": "Response status tracking and queue metrics.",
    },
    {
        "name": "Historical Ingestion",
        "description": "Dry runs, execution, auditing, and removal of historical knowledge.",
    },
    {
        "name": "SolidSET Agents",
        "description": "Agents, workrooms, models, private knowledge, and multi-agent execution.",
    },
    {
        "name": "SolidSET Configuration",
        "description": "SolidSET instances and master-data synchronization.",
    },
    {"name": "LLM Providers", "description": "AI model and provider configuration."},
    {
        "name": "Learning and Feedback",
        "description": "Feedback, reactions, reinforcement signals, and learning evaluation.",
    },
    {
        "name": "Audio, History and Context",
        "description": "Generated audio, conversation history, and user context.",
    },
    {
        "name": "Observability",
        "description": "Health, metrics, recent messages, and internal diagnostics.",
    },
    {
        "name": "Connectivity",
        "description": "Connectivity checks for configured external services.",
    },
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


@app.middleware("http")
async def prioritize_interactive_requests(request: Request, call_next):
    """Hace que las ingestas cedan recursos durante generación interactiva."""
    interactive_paths = (
        "/api/v1/agent/dialogue",
        "/api/v1/agent/notification/chat-question/suggest-response",
    )
    if request.url.path not in interactive_paths:
        return await call_next(request)
    with interactive_work("http"):
        return await call_next(request)


app.state.agent = agent
app.include_router(agent_prompts_router)
app.include_router(ingestion_router)
app.include_router(synchronization_router)
app.include_router(llm_configuration_router)
app.include_router(responses_router)
app.include_router(diagnostics.router)
app.include_router(history_router)
feedback.configure(agent)
app.include_router(feedback.router)
app.include_router(conversation.router)
app.include_router(notifications.router)
app.include_router(suggestions_controller.router)
agent_management.configure(agent)
app.include_router(agent_management.router)
app.include_router(solidset_instances_router)
dialogue_runtime.configure(app)
conversation.configure(app, orchestrator)
diagnostics.configure(app, agent, notification_listener)
notifications.configure(notification_listener)
suggestions_controller.configure(suggestion_queue)
configure_openapi(app, OPENAPI_TAGS)

_dialogue_slots = dialogue_runtime.slots


def _ingestar_instancias_solidset_activas() -> dict[str, Any]:
    """Ejecuta la ingesta periódica dentro del contexto aislado de cada instancia."""
    instances = list_active_solidset_instances()
    eligible = [
        instance
        for instance in instances
        if (instance.get("DataAPI") or {}).get("active")
        and str((instance.get("DataAPI") or {}).get("BaseUrl") or "").strip()
    ]
    if not eligible:
        raise RuntimeError(
            "Não existem instâncias SolidSET ativas com uma SolidSET Data API configurada."
        )

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for instance in eligible:
        instance_code = str(instance.get("Code") or instance.get("ID") or "unknown")
        print(f"🔄 Iniciando aprendizagem BD instance={instance_code}")
        try:
            with solidset_sql_instance_context(instance):
                results[instance_code] = ingestar_sistema_completo(
                    instance_code=instance_code,
                )
        except Exception as exc:
            errors[instance_code] = str(exc)
            print(f"⚠️ Aprendizagem BD falhou instance={instance_code}: {exc}")

    if not results:
        failed_codes = ", ".join(sorted(errors)) or "unknown"
        raise RuntimeError(
            f"A aprendizagem BD falhou em todas as instâncias: {failed_codes}."
        )
    return {
        "status": "completed" if not errors else "partial",
        "instances": results,
        "errors": errors,
    }


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
                wait_seconds = min(
                    intervalo, max(5, settings.DB_STUDY_IDLE_CHECK_SECONDS)
                )
                print(
                    f"⏸️ Ingesta BD diferida por {chats_activos} conversación(es) activa(s). "
                    f"Revisando de nuevo en {wait_seconds}s"
                )
                await asyncio.sleep(wait_seconds)
                continue

            ingesta = asyncio.to_thread(_ingestar_instancias_solidset_activas)
            if settings.DB_STUDY_MAX_RUN_SECONDS > 0:
                resultado = await asyncio.wait_for(
                    ingesta, timeout=settings.DB_STUDY_MAX_RUN_SECONDS
                )
            else:
                resultado = await ingesta

            app.state.last_db_study_at = datetime.utcnow().isoformat()
            app.state.last_db_study_result = resultado
            app.state.last_db_study_error = None
            consecutive_failures = 0
            print(f"✅ Aprendizaje BD completado: {resultado}")
            wait_seconds = intervalo
        except asyncio.TimeoutError:
            app.state.last_db_study_error = f"Ingesta excedió el tiempo máximo de {settings.DB_STUDY_MAX_RUN_SECONDS}s"
            # No castigamos exponencialmente un timeout de una tarea que sigue completándose en background.
            consecutive_failures = 0
            wait_seconds = intervalo
            print(f"⚠️ {app.state.last_db_study_error}")
            print(
                f"⏳ Reintentando aprendizaje BD en {wait_seconds}s (timeout controlado, no se aplica backoff)"
            )
        except Exception as exc:
            app.state.last_db_study_error = str(exc)
            consecutive_failures += 1
            backoff_factor = min(2 ** min(consecutive_failures, 4), 16)
            wait_seconds = intervalo * backoff_factor
            print(f"⚠️ Error en aprendizaje continuo desde BD: {exc}")
            print(
                f"⏳ Reintentando aprendizaje BD en {wait_seconds}s (fallos consecutivos: {consecutive_failures})"
            )

        await asyncio.sleep(wait_seconds)


async def _ciclo_notificaciones_api() -> None:
    """Escucha en segundo plano la API de notificaciones para enriquecer contexto."""
    intervalo = max(10, settings.NOTIF_API_POLL_SECONDS)
    if not notification_listener.is_enabled():
        print(
            "ℹ️ Listener de notificaciones deshabilitado (define NOTIF_API_BASE_URL para activarlo)."
        )
        return

    initial_delay = max(0, settings.NOTIF_API_START_DELAY_SECONDS)
    if initial_delay > 0:
        print(
            f"⏳ Listener Notification API iniciará en {initial_delay}s para priorizar arranque de diálogo"
        )
        await asyncio.sleep(initial_delay)

    print(f"🔔 Listener Notification API activo cada {intervalo} segundos")

    while True:
        try:
            # Evita competir con diálogos activos cuando el modo de pausa está habilitado.
            chats_activos = _get_active_dialogues()
            should_pause_for_dialogue = (
                settings.NOTIF_PAUSE_DURING_DIALOGUE and chats_activos > 0
            )
            if should_pause_for_dialogue:
                await asyncio.sleep(
                    min(intervalo, max(5, settings.DB_STUDY_IDLE_CHECK_SECONDS))
                )
                continue

            # Fallback: incluso con la pausa deshabilitada, no competir bajo carga máxima.
            max_dialogues = max(1, settings.DIALOGUE_MAX_CONCURRENT)
            if chats_activos >= max_dialogues:
                await asyncio.sleep(
                    min(intervalo, max(5, settings.DB_STUDY_IDLE_CHECK_SECONDS))
                )
                continue

            resultado = await notification_listener.pull_once()
            auto_reply_sent = await _process_auto_replies(
                resultado.get("auto_reply_candidates") or []
            )
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
        max_schema_attempts = 10
        for schema_attempt in range(max_schema_attempts):
            try:
                await asyncio.to_thread(ensure_llm_provider_schema)
                await asyncio.to_thread(ensure_solidset_agent_resource_schema)
                await asyncio.to_thread(ensure_agent_model_schema)
                await asyncio.to_thread(ensure_agent_response_audit_schema)
                if settings.TOOL_AUDIT_ENABLED:
                    await asyncio.to_thread(ensure_agent_tool_audit_schema)
                await asyncio.to_thread(ensure_historical_schema)
                quarantined = await asyncio.to_thread(quarantine_legacy_generated_knowledge)
                if quarantined:
                    print(
                        "🧹 Conocimiento legado generado por IA puesto en cuarentena "
                        f"rows={quarantined}"
                    )
                break
            except Exception as exc:
                if schema_attempt < max_schema_attempts - 1:
                    print(f"⏳ Esperando inicio de PostgreSQL (intento {schema_attempt + 1}/{max_schema_attempts}): {exc}")
                    await asyncio.sleep(2.0)
                else:
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
                app.state.notification_warmup = (
                    await notification_listener.warmup_session()
                )
            except Exception as exc:
                app.state.notification_warmup = {
                    "enabled": True,
                    "logged_in": False,
                    "error": str(exc),
                }
                print(f"⚠️ Warmup SOLIDSET listener falló: {exc}")
        app.state.db_study_task = asyncio.create_task(_ciclo_aprendizaje_bd())
        if settings.NOTIF_API_BACKGROUND_ENABLED:
            app.state.notification_task = asyncio.create_task(
                _ciclo_notificaciones_api()
            )
        else:
            print(
                "ℹ️ Listener Notification API en background desactivado por rendimiento (NOTIF_API_BACKGROUND_ENABLED=false)"
            )


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


# Public Swagger examples must never expose identifiers copied from a real
# SolidSET notification. Keep this concise sample intentionally fictitious.


# ============================================================
# SEGURIDAD: FILTROS CONTRA PROMPT INJECTION
# ============================================================


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


# ============================================================
# ✅ NUEVO ENDPOINT: PROBAR CONECTIVIDAD CON SOLIDSET API
# ============================================================


# ============================================================
# PUNTO DE ENTRADA PARA EJECUCIÓN DIRECTA
# ============================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
