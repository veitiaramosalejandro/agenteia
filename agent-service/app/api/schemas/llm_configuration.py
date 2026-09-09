from datetime import datetime
from typing import Optional, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LLMProviderFromEnvironment(BaseModel):
    Source: Literal["runtime", "openai_search", "nvidia"] = Field(
        "runtime", description="runtime: copia LLM_PROVIDER y MODEL_NAME. openai_search: usa OpenAI. nvidia: usa NVIDIA_API_KEY y Kimi K3."
    )
    Code: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$",
                      description="Código único de la conexión. Úsalo después como ProviderCode al asignarla a un agente.", examples=["openai-search"])
    Name: str = Field(..., min_length=1, max_length=255, pattern=r"\S",
                      description="Nombre visible que describe para qué sirve la conexión.", examples=["OpenAI para búsquedas externas"])
    IsDefault: bool = Field(False, description="true sustituye la conexión global predeterminada. Mantén false para una conexión de búsqueda o especializada.")

    class Config:
        extra = "forbid"
        json_schema_extra = {"examples": [{"Source": "openai_search", "Code": "openai-search", "Name": "OpenAI para búsquedas externas", "IsDefault": False}]}


class LLMProviderConfiguration(BaseModel):
    Code: str = Field(..., min_length=1, max_length=80, description="Código único de la conexión; se utiliza como ProviderCode al asignarla a un agente.")
    Name: str = Field(..., min_length=1, max_length=255, description="Nombre visible y descriptivo de la conexión.")
    Provider: str = Field(..., min_length=1, max_length=40, description="Tipo de servicio: ollama, openai, azure_openai, anthropic, gemini, local_openai u openai_compatible.")
    Model: str = Field(..., min_length=1, max_length=255, description="Identificador exacto del modelo que ofrece el proveedor, por ejemplo gpt-4.1-mini o qwen2.5:3b.")
    BaseUrl: Optional[str] = Field(None, max_length=500, description="URL base del servicio; no incluyas /chat/completions ni /responses.")
    APIKey: Optional[str] = Field(None, max_length=8000, description="Clave secreta del proveedor. Se cifra al guardar y no se devuelve. Omítela al actualizar para conservar la existente.")
    Temperature: float = Field(0.5, ge=0, le=2, description="Variación de las respuestas: valores bajos favorecen consistencia; valores altos, diversidad. Depende del modelo.")
    MaxOutputTokens: int = Field(1024, gt=0, le=131072, description="Máximo de tokens de salida por respuesta; no equivale a palabras.")
    TimeoutSeconds: int = Field(60, gt=0, le=3600, description="Tiempo máximo de espera de una petición, en segundos.")
    AzureEndpoint: Optional[str] = Field(None, max_length=500, description="Solo Azure OpenAI: URL del recurso de Azure.")
    AzureApiVersion: Optional[str] = Field(None, max_length=80, description="Solo Azure OpenAI: versión de la API admitida por el recurso.")
    AzureDeployment: Optional[str] = Field(None, max_length=255, description="Solo Azure OpenAI: nombre del despliegue creado en Azure.")
    OpenAIOrganization: Optional[str] = Field(None, max_length=255, description="Organización OpenAI opcional asociada a la clave.")
    OpenAIProject: Optional[str] = Field(None, max_length=255, description="Proyecto OpenAI opcional asociado a la clave.")
    UseResponsesAPI: bool = Field(True, description="Utilizar Responses API para modelos OpenAI compatibles.")
    StoreResponses: bool = Field(False, description="Permitir que OpenAI almacene respuestas; false evita solicitar ese almacenamiento.")
    MaxRetries: int = Field(2, ge=0, le=5, description="Número máximo de reintentos de la petición ante errores transitorios.")
    ServiceTier: str = Field("auto", pattern="^(auto|default|flex|priority|fast)$", description="Nivel de servicio OpenAI. auto utiliza el nivel disponible; otros niveles dependen de la cuenta y del modelo.")
    IsDefault: bool = Field(False, description="true establece la conexión global predeterminada; false para conexiones especializadas.")
    active: bool = Field(True, description="Habilita la conexión para su selección por los agentes.")

    class Config:
        extra = "forbid"



class LLMProviderConfigurationStored(BaseModel):
    ID: UUID
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
    OpenAIOrganization: Optional[str] = None
    OpenAIProject: Optional[str] = None
    UseResponsesAPI: bool = True
    StoreResponses: bool = False
    MaxRetries: int = 2
    ServiceTier: str = "auto"
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
    TrainingMode: str = Field(
        "rag_reinforcement", pattern="^(rag_reinforcement|rag_only|disabled)$"
    )
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
    ID: UUID
    IDResource: UUID
    IDProviderConfiguration: UUID
    CreatedAt: datetime
    UpdatedAt: datetime
