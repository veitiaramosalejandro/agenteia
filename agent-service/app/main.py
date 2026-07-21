import os
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.agent.core import MachiningAgent
from app.agent.speech import text_to_speech

app = FastAPI(title="Machining Assistant Agent API")
agent = MachiningAgent()

# Modelo de datos recibido desde el Frontend / UI
class ChatConversationRequest(BaseModel):
    session_id: str
    message: str             # Texto enviado por el operario
    generate_audio: bool = False

# Manejador para ver errores de validación claros en consola si falta algún parámetro
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print("❌ Error de validación en la petición recibida:", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )

@app.post("/api/v1/agent/dialogue")
def handle_dialogue(req: ChatConversationRequest):
    try:
        # 1. El agente procesa la solicitud con su memoria de sesión y herramientas
        response_text = agent.analyze_event_with_dialogue(
            session_id=req.session_id, 
            user_text=req.message
        )
        
        result = {
            "session_id": req.session_id,
            "user_message": req.message,
            "agent_response": response_text
        }
        
        # 2. Si la interfaz solicita respuesta de voz
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