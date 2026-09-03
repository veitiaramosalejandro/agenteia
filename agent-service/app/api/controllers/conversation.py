from __future__ import annotations

import os
import threading
import uuid
from time import perf_counter
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, HTTPException, status

from app.agent.authorization import resolve_resource_table
from app.agent.speech import text_to_speech
from app.api.schemas.common import (
    ChatConversationRequest,
    ChatConversationResponse,
    FrameworkMessageDTO,
)
from app.api.examples import _FRAMEWORK_MESSAGE_EXAMPLES
from app.config import settings
from app.services import dialogue_runtime
from app.services.auto_reply import _get_payload_value, _payload_has_talk_with_agent
from app.services.dialogue_runtime import (
    active_count as _get_active_dialogues,
    build_cache_key as _build_dialogue_cache_key,
    finish as _finish_dialogue,
    get_cached as _get_cached_dialogue_response,
    metrics_snapshot as _get_dialogue_metrics_snapshot,
    record_metrics as _record_dialogue_metrics,
    release_when_done as _release_dialogue_resources_when_done,
    resolve_canal_id as _resolve_effective_canal_id,
    start as _start_dialogue,
    store_cached as _store_cached_dialogue_response,
)
from app.services.response_status import create as _create_response_status
from app.services.response_status import update as _update_response_status


router = APIRouter()
app = None
orchestrator = None
_dialogue_slots = dialogue_runtime.slots


def configure(runtime_app: Any, runtime_orchestrator: Any) -> None:
    global app, orchestrator
    app = runtime_app
    orchestrator = runtime_orchestrator


PALABRAS_PROHIBIDAS = [
    "olvida",
    "ignora",
    "nuevas instrucciones",
    "system prompt",
    "contraseña",
    "password",
    "administrador",
    "admin",
    "root",
    "sysadmin",
    "cambia tu rol",
    "nuevo rol",
    "actúa como",
    "eres ahora",
    "desde ahora",
    "ignora tus",
    "sobreescribe",
    "reemplaza",
    "borra tus",
    "elimina tus",
    "reset",
    "reinicia",
    "desobedece",
    "salta",
    "bypass",
    "hack",
    "exploit",
]
PALABRAS_SQL_INYECCION = [
    "drop",
    "delete",
    "insert",
    "update",
    "alter",
    "truncate",
    "exec",
    "execute",
    "xp_",
    "sp_",
    "union",
    "select.*into",
    "bulk",
    "backup",
    "restore",
    "shutdown",
]


def _valid_framework_identifier(value: Any) -> Optional[str]:
    """Descarta identificadores vacíos que SolidSET usa como valor nulo."""
    normalized = str(value or "").strip()
    if not normalized or normalized in {
        "0",
        "00000000-0000-0000-0000-000000000000",
    }:
        return None
    return normalized


def _framework_message_to_dialogue(
    message: FrameworkMessageDTO,
) -> ChatConversationRequest:
    """Normaliza el contrato de Notification al contrato interno del diálogo."""
    sender = message.Sender or {}
    destiny = message.Destiny or {}
    chat = message.Chat if isinstance(message.Chat, dict) else {}
    # La identidad funcional del interlocutor en SolidSET es IDResource. IDLogin
    # sirve para autenticar, pero no debe sustituir al recurso cuando ambos llegan.
    user_id = _valid_framework_identifier(
        _get_payload_value(sender, "resource", "IDResource")
    ) or _valid_framework_identifier(_get_payload_value(sender, "login", "IDLogin"))
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
        or _valid_framework_identifier(
            _get_payload_value(sender, "session", "IDSession")
        )
        or _valid_framework_identifier(
            _get_payload_value(destiny, "session", "IDSession")
        )
        or _valid_framework_identifier(_get_payload_value(chat, "idChat2", "IDChat2"))
        or _valid_framework_identifier(
            _get_payload_value(sender, "conversationId", "IDConversation")
        )
        or _valid_framework_identifier(
            _get_payload_value(destiny, "conversationId", "IDConversation")
        )
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
        login_id=_valid_framework_identifier(
            _get_payload_value(sender, "login", "IDLogin")
        ),
        canal_id=canal_id,
        generate_audio=False,
    )


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
        "puta",
        "puto",
        "mierda",
        "cabrón",
        "cabrona",
        "hijo de puta",
        "pendejo",
        "pendeja",
        "chinga",
        "chingar",
        "verga",
        "culero",
        "culera",
        "malparido",
        "malparida",
        "gonorrea",
        "maricón",
        "maricon",
        "marica",
        "joder",
        "hostia",
        "coño",
        "cojones",
    ]
    text_lower = text.lower()
    for palabra in palabras_ofensivas:
        if palabra in text_lower:
            return True
    return False


@router.post("/api/v1/agent/dialogue", response_model=ChatConversationResponse)
def handle_dialogue(
    message: Annotated[
        FrameworkMessageDTO,
        Body(openapi_examples=_FRAMEWORK_MESSAGE_EXAMPLES),
    ],
):

    print(message.model_dump_json(indent=2))

    """
    Procesa un FrameworkMessage como diálogo con el agente.
    
    - Normaliza RawMessage, Sender y Destiny al contexto interno del diálogo
    - Valida la seguridad del mensaje
    - Obtiene el contexto del usuario (sistema de aprendizaje)
    - Procesa la consulta con el agente
    """
    chat_payload = message.Chat if isinstance(message.Chat, dict) else {}
    dialogue_payload = message.model_dump(mode="json")
    if not _payload_has_talk_with_agent(dialogue_payload):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "O agente IA só pode responder quando Chat.resourceTable inclui um recurso "
                "de tipo 2 com talkWithAgent=true. O endpoint chat-question/suggest-response "
                "é a única exceção para conversas internas de sugestões."
            ),
        )
    if (
        message.RawMessage is None
        and _get_payload_value(chat_payload, "rawMessage", "RawMessage") is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="RawMessage ou Chat.rawMessage é obrigatório para processar um FrameworkMessage em /dialogue.",
        )

    req = _framework_message_to_dialogue(message)

    dialogue_started = False
    slot_acquired = False
    request_started_at = perf_counter()
    effective_canal_id = _resolve_effective_canal_id(req.canal_id, req.session_id)
    cache_key = _build_dialogue_cache_key(
        req.session_id, req.user_id, effective_canal_id, req.message
    )
    try:
        # --- 1. VALIDACIONES DE SEGURIDAD ---

        # Validar que el mensaje no esté vacío.
        # Em modo "conselhos à minha IA" (advice_mode), o cliente abre a sessão sem texto:
        # em vez do placeholder genérico, arranca o diálogo com um pedido implícito de sugestão.
        advice_mode = str(
            (message.Info or {}).get("advice_mode") or ""
        ).strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if not req.message or req.message.strip() == "":
            if advice_mode:
                req.message = (
                    "Quero aconselhar a minha IA. "
                    "Sugere por onde começar e que orientações iniciais me dás."
                )
                cache_key = _build_dialogue_cache_key(
                    req.session_id, req.user_id, effective_canal_id, req.message
                )
            else:
                return ChatConversationResponse(
                    session_id=req.session_id,
                    user_message=req.message,
                    agent_response="Por favor, escreva uma mensagem para que eu possa ajudar.",
                )

        # Validar largo del mensaje (prevenir abusos)
        if len(req.message) > 5000:
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message[:100] + "...",
                agent_response="A mensagem é demasiado longa. Reduza o pedido para menos de 5000 caracteres.",
            )

        # Detectar inyección de prompts
        if detect_prompt_injection(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="Não posso processar este pedido devido às políticas de segurança. Reformule o pedido técnico de forma clara e direta.",
            )

        # Detectar inyección SQL
        if detect_sql_injection(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="Foi detetada uma tentativa de injeção SQL. Apenas posso executar consultas de leitura (SELECT) seguras. Indique os dados que pretende consultar.",
            )

        # Detectar contenido ofensivo
        if detect_offensive_content(req.message):
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="Mantenha um tom respeitador na conversa. Estou disponível para ajudar com questões técnicas sobre maquinaria e sistemas.",
            )

        # Estado de progresso (mesmos textos do chat: A aguardar… / A pensar… / …)
        info = message.Info or {}
        dialogue_request_id = str(info.get("request_id") or "").strip() or str(
            uuid.uuid4()
        )
        _create_response_status(dialogue_request_id, dialogue_request_id, 1)
        _update_response_status(dialogue_request_id, "queued")

        cached_response_text = _get_cached_dialogue_response(cache_key)
        if cached_response_text:
            elapsed = perf_counter() - request_started_at
            _record_dialogue_metrics(elapsed, cache_hit=True)
            print(
                f"⚡ Cache HIT en diálogo (sesión: {req.session_id}) -> {elapsed:.2f}s"
            )
            _update_response_status(dialogue_request_id, "completed", response_count=1)
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
        slot_acquired = _dialogue_slots.acquire(
            timeout=max(0, settings.DIALOGUE_ADMISSION_TIMEOUT_SECONDS)
        )
        if not slot_acquired:
            _update_response_status(
                dialogue_request_id,
                "failed",
                error="O agente está a processar várias conversas neste momento.",
            )
            return ChatConversationResponse(
                session_id=req.session_id,
                user_message=req.message,
                agent_response="O agente está a processar várias conversas neste momento. Tente novamente dentro de alguns segundos.",
            )

        # Registrar inicio del procesamiento
        print(
            f"📨 Procesando consulta de usuario {req.user_id} (sesión: {req.session_id})"
        )
        print(f"   Mensaje: {req.message[:100]}...")
        _update_response_status(dialogue_request_id, "processing")

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

        _update_response_status(dialogue_request_id, "thinking")
        worker = threading.Thread(target=_run_agent_dialogue, daemon=True)
        worker.start()
        worker.join(timeout=processing_timeout)

        if worker.is_alive():
            print(
                f"⚠️ Timeout de conversación en sesión {req.session_id} tras {processing_timeout}s. "
                "Se devuelve respuesta controlada y se libera al terminar en segundo plano."
            )
            _update_response_status(
                dialogue_request_id,
                "failed",
                error="O pedido está a demorar mais do que o esperado.",
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
                agent_response="O pedido está a demorar mais do que o esperado. Tente novamente dentro de alguns segundos.",
            )

        if "error" in error_holder:
            _update_response_status(
                dialogue_request_id, "failed", error=str(error_holder["error"])
            )
            raise error_holder["error"]

        response_text = response_holder.get("text", "")
        _update_response_status(dialogue_request_id, "sending")

        _store_cached_dialogue_response(cache_key, response_text)

        # --- 3. CONSTRUIR RESPUESTA ---

        result = ChatConversationResponse(
            session_id=req.session_id,
            user_message=req.message,
            agent_response=response_text,
        )

        # Generar audio si se solicita
        if req.generate_audio and response_text:
            try:
                audio_path = text_to_speech(response_text)
                result.audio_url = (
                    f"/api/v1/agent/audio-response?file={os.path.basename(audio_path)}"
                )
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
        _update_response_status(dialogue_request_id, "completed", response_count=1)
        return result

    except Exception as e:
        print(f"❌ Error crítico en /dialogue: {str(e)}")
        try:
            rid = str((message.Info or {}).get("request_id") or "").strip()
            if rid:
                _update_response_status(rid, "failed", error=str(e))
        except Exception:
            pass
        # Capturar error y devolver mensaje amigable
        return ChatConversationResponse(
            session_id=req.session_id,
            user_message=req.message,
            agent_response="Ocorreu um erro ao processar o pedido. Tente novamente ou contacte o administrador do sistema.",
        )
    finally:
        if dialogue_started:
            _finish_dialogue()
            app.state.active_dialogues = _get_active_dialogues()
        if slot_acquired:
            _dialogue_slots.release()
