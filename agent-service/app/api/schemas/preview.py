"""Public response contract for generation without sending to SolidSET."""

from typing import Any

from pydantic import BaseModel, Field


class PreviewAgentResponse(BaseModel):
    AgentResourceId: str
    AgentName: str
    Response: str = Field(description="Texto final después de los filtros de salida.")


class FrameworkMessagePreviewResponse(BaseModel):
    Result: int = 0
    Learned: int
    Skipped: int
    Response: str | None = Field(
        default=None,
        description="Respuesta final; varias respuestas se separan con dos saltos de línea. "
                    "Null si no se obtuvo respuesta. Independiente de PayloadCount.",
    )
    Responses: list[PreviewAgentResponse] = Field(
        default_factory=list, description="Respuestas por agente, en orden de generación."
    )
    PayloadCount: int = Field(description="Número de payloads preparados, no de respuestas generadas.")
    Payloads: list[dict[str, Any]] = Field(default_factory=list)
