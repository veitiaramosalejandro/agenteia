import os
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from langchain_community.chat_message_histories import RedisChatMessageHistory
from app.config import settings

from app.agent.core import MachiningAgent
from app.agent.speech import text_to_speech

app = FastAPI(title="Machining Assistant Agent API")
agent = MachiningAgent()

class ChatConversationRequest(BaseModel):
    session_id: str
    message: str
    generate_audio: bool = False

# 🚨 NUEVO: Filtro de seguridad contra Prompt Injection
PALABRAS_PROHIBIDAS = [
    "olvida", "ignora", "nuevas instrucciones", "system prompt", "contraseña",
    "password", "administrador", "admin", "root", "sysadmin", "cambia tu rol",
    "nuevo rol", "actúa como", "eres ahora", "desde ahora"
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
    sql_keywords = ["drop", "delete", "insert", "update", "alter", "truncate", "exec", "execute", "xp_", "sp_"]
    text_lower = text.lower()
    # Si el usuario menciona SQL en contexto normal, no bloquear
    if "select" in text_lower:
        for kw in sql_keywords:
            if kw in text_lower:
                return True
    return False

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print("❌ Error de validación:", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )

@app.post("/api/v1/agent/dialogue")
def handle_dialogue(req: ChatConversationRequest):
    try:
        # 🚨 NUEVA VALIDACIÓN DE SEGURIDAD
        if detect_prompt_injection(req.message):
            return {
                "session_id": req.session_id,
                "user_message": req.message,
                "agent_response": "🚫 Lo siento, no puedo procesar esa solicitud por políticas de seguridad. Si necesitas ayuda con tu consulta técnica, reformúlala."
            }
        
        if detect_sql_injection(req.message):
            return {
                "session_id": req.session_id,
                "user_message": req.message,
                "agent_response": "🔒 He detectado un intento de inyección SQL. Solo puedo ejecutar consultas de lectura (SELECT) seguras. ¿Qué información necesitas consultar?"
            }

        response_text = agent.analyze_event_with_dialogue(
            session_id=req.session_id, 
            user_text=req.message
        )
        
        result = {
            "session_id": req.session_id,
            "user_message": req.message,
            "agent_response": response_text
        }
        
        if req.generate_audio:
            audio_path = text_to_speech(response_text)
            result["audio_url"] = f"/api/v1/agent/audio-response?file={os.path.basename(audio_path)}"
            
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/agent/audio-response")
def get_audio_file(file: str):
    file_path = os.path.join("/tmp", file)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="Archivo de audio no encontrado.")

@app.get("/api/v1/agent/history/{session_id}")
def get_chat_history(session_id: str):
    try:
        history = RedisChatMessageHistory(session_id, url=settings.REDIS_URL)
        messages = []
        for msg in history.messages:
            role = "user" if msg.type in ["human", "user"] else "assistant"
            messages.append({"role": role, "content": msg.content})
        
        return {"session_id": session_id, "messages": messages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error recuperando historial: {str(e)}")