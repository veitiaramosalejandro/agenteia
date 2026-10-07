from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel

class RecursoHumano(BaseModel):
    """Modelo de un recurso humano en el sistema."""
    id: str
    nombre: str
    email: str
    rol: str  # Ej: "operario", "supervisor", "ingeniero", "gerente"
    canales: List[str]  # IDs de canales a los que tiene acceso
    departamento: Optional[str] = None
    especialidades: List[str] = []  # Ej: ["torno", "fresadora", "mantenimiento"]

class RecursoMaterial(BaseModel):
    """Modelo de un recurso material en el sistema."""
    id: str
    nombre: str
    tipo: str  # Ej: "maquina", "herramienta", "material", "software"
    canal_id: str
    estado: str  # "disponible", "en_uso", "mantenimiento"
    especificaciones: Dict[str, Any] = {}

class Canal(BaseModel):
    """Modelo de un canal de trabajo."""
    id: str
    nombre: str
    descripcion: str
    tipo: str  # "produccion", "mantenimiento", "calidad", "diseno"
    recursos_humanos: List[str]  # IDs de recursos humanos asignados
    recursos_materiales: List[str]  # IDs de recursos materiales asignados
    proyectos_activos: List[str] = []

class Actividad(BaseModel):
    """Registro de actividad de un recurso humano."""
    id: str
    recurso_humano_id: str
    canal_id: str
    tipo: str  # "tarea", "reporte", "diagnostico", "mantenimiento"
    descripcion: str
    timestamp: datetime
    metadatos: Dict[str, Any] = {}

class ContextoUsuario(BaseModel):
    """Contexto completo de un usuario para el agente."""
    usuario: RecursoHumano
    canales_acceso: List[Canal]
    actividades_recientes: List[Actividad]
    recursos_disponibles: List[RecursoMaterial]
    permisos: List[str]  # Ej: ["ver_telemetria", "modificar_parametros"]