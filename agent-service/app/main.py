import asyncio
import os
from contextlib import suppress
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from langchain_community.chat_message_histories import RedisChatMessageHistory
from app.config import settings

from app.agent.core import MachiningAgent
from app.agent.speech import text_to_speech
from app.system.ingest import ingestar_sistema_completo

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


async def _ciclo_aprendizaje_bd() -> None:
    """Mantiene al agente actualizándose con datos recientes de la base de datos."""
    intervalo = max(60, settings.DB_STUDY_INTERVAL_SECONDS)
    print(f"🔄 Ciclo de aprendizaje BD activo cada {intervalo} segundos")
    consecutive_failures = 0

    while True:
        try:
            resultado = await asyncio.to_thread(ingestar_sistema_completo)
            app.state.last_db_study_at = datetime.utcnow().isoformat()
            app.state.last_db_study_result = resultado
            app.state.last_db_study_error = None
            consecutive_failures = 0
            print(f"✅ Aprendizaje BD completado: {resultado}")
            wait_seconds = intervalo
        except Exception as exc:
            app.state.last_db_study_error = str(exc)
            consecutive_failures += 1
            backoff_factor = min(2 ** min(consecutive_failures, 4), 16)
            wait_seconds = intervalo * backoff_factor
            print(f"⚠️ Error en aprendizaje continuo desde BD: {exc}")
            print(f"⏳ Reintentando aprendizaje BD en {wait_seconds}s (fallos consecutivos: {consecutive_failures})")

        await asyncio.sleep(wait_seconds)


@app.on_event("startup")
async def startup_db_learning() -> None:
    """Lanza la tarea de aprendizaje continuo desde la base de datos."""
    if getattr(app.state, "db_study_task", None) is None:
        app.state.last_db_study_at = None
        app.state.last_db_study_result = None
        app.state.last_db_study_error = None
        app.state.db_study_task = asyncio.create_task(_ciclo_aprendizaje_bd())


@app.on_event("shutdown")
async def shutdown_db_learning() -> None:
    """Detiene la tarea de aprendizaje continuo al apagar el servicio."""
    task = getattr(app.state, "db_study_task", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        app.state.db_study_task = None

# ============================================================
# MODELOS DE DATOS
# ============================================================

class ChatConversationRequest(BaseModel):
    session_id: str = Field(..., description="ID de la sesión de conversación")
    message: str = Field(..., description="Mensaje enviado por el usuario")
    user_id: str = Field(..., description="ID del usuario que está consultando (recurso humano)")
    canal_id: Optional[str] = Field(None, description="ID del canal actual (opcional)")
    generate_audio: bool = Field(False, description="Si se debe generar audio de la respuesta")

class ChatConversationResponse(BaseModel):
    session_id: str
    user_message: str
    agent_response: str
    audio_url: Optional[str] = None
    user_context_used: Optional[str] = None  # Para debugging

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

@app.post("/api/v1/agent/dialogue", response_model=ChatConversationResponse)
def handle_dialogue(req: ChatConversationRequest):
    """
    Procesa un diálogo con el agente.
    
    - Valida la seguridad del mensaje
    - Obtiene el contexto del usuario (sistema de aprendizaje)
    - Procesa la consulta con el agente
    - Genera audio si se solicita
    """
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

        # --- 2. PROCESAR CON EL AGENTE ---
        
        # Registrar inicio del procesamiento
        print(f"📨 Procesando consulta de usuario {req.user_id} (sesión: {req.session_id})")
        print(f"   Mensaje: {req.message[:100]}...")
        
        # Ejecutar el agente con el contexto del usuario
        response_text = agent.analyze_event_with_dialogue(
            session_id=req.session_id, 
            user_text=req.message,
            user_id=req.user_id,
            canal_id=req.canal_id,
        )
        
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
        return result
        
    except Exception as e:
        print(f"❌ Error crítico en /dialogue: {str(e)}")
        # Capturar error y devolver mensaje amigable
        return ChatConversationResponse(
            session_id=req.session_id,
            user_message=req.message,
            agent_response=f"⚠️ Lo siento, ocurrió un error al procesar tu consulta. Por favor, intenta nuevamente o contacta al administrador del sistema. (Error: {str(e)[:100]})"
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
def get_chat_history(session_id: str):
    """
    Recupera el historial de conversación de una sesión específica desde Redis.
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
        
        return {
            "session_id": session_id, 
            "messages": messages,
            "total_messages": len(messages)
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
        }
    }


@app.get("/api/v1/agent/context/{user_id}")
def get_user_context(user_id: str):
    """
    Devuelve el contexto completo de un usuario (para debugging y validación).
    """
    try:
        from app.system.learning import SistemaAprendizaje
        sistema = SistemaAprendizaje()
        contexto = sistema.obtener_contexto_usuario(user_id)
        
        if contexto:
            return {
                "user_id": user_id,
                "context": contexto.dict(),
                "canales_count": len(contexto.canales_acceso),
                "actividades_count": len(contexto.actividades_recientes),
                "recursos_count": len(contexto.recursos_disponibles)
            }
        else:
            return {
                "user_id": user_id,
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