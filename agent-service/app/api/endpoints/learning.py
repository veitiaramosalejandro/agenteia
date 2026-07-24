from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.system.learning import SistemaAprendizaje

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])
sistema = SistemaAprendizaje()

class ActividadRegistro(BaseModel):
    recurso_humano_id: str
    canal_id: str
    tipo: str
    descripcion: str
    metadatos: dict = {}

@router.post("/actividad")
def registrar_actividad(data: ActividadRegistro):
    """Registra una actividad para que el agente aprenda de ella."""
    from app.system.schema import Actividad
    from datetime import datetime
    
    actividad = Actividad(
        id=f"act_{datetime.now().timestamp()}",
        recurso_humano_id=data.recurso_humano_id,
        canal_id=data.canal_id,
        tipo=data.tipo,
        descripcion=data.descripcion,
        timestamp=datetime.now(),
        metadatos=data.metadatos
    )
    
    if sistema.aprender_actividad(actividad):
        return {"status": "success", "message": "Actividad aprendida correctamente"}
    else:
        raise HTTPException(status_code=500, detail="Error aprendiendo actividad")

@router.get("/colaboradores/{canal_id}/{tipo_actividad}")
def sugerir_colaboradores(canal_id: str, tipo_actividad: str):
    """Sugiere colaboradores para un tipo de actividad en un canal."""
    colaboradores = sistema.sugerir_colaboradores(canal_id, tipo_actividad)
    return {"colaboradores": colaboradores}