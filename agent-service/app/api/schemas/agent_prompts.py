from datetime import datetime
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints


PromptListItem = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class AgentPromptGenerateRequest(BaseModel):
    name: str = Field("Plantilla del agente", min_length=1, max_length=255)
    # A role is a bounded professional identity, not merely a short role code.
    role: str = Field("asistente general", min_length=1, max_length=1000)
    objective: str = Field(
        "Ayudar a los usuarios autorizados de SolidSET.", min_length=1, max_length=2000
    )
    specialties: list[str] = Field(default_factory=list, max_length=20)
    code_review_instructions: list[PromptListItem] = Field(
        default_factory=list, max_length=20
    )
    response_format: list[PromptListItem] = Field(default_factory=list, max_length=20)
    restrictions: list[PromptListItem] = Field(
        default_factory=list, max_length=20
    )
    out_of_scope_action: str = Field("", max_length=1000)
    tone: str = Field("profesional y cercano", min_length=1, max_length=200)
    response_style: str = Field(
        "directo, claro y basado en evidencias", min_length=1, max_length=300
    )
    default_language: str = Field("pt", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    created_by: str = Field("manual", min_length=1, max_length=255)

    class Config:
        extra = "forbid"


class AgentPromptStoredResponse(BaseModel):
    ID: UUID
    IDSolidSETInstance: UUID
    IDResource: UUID
    Version: int
    Name: str
    SystemPrompt: str
    BehaviorConfig: dict[str, Any]
    Status: str
    CreatedBy: Optional[str] = None
    CreatedAt: datetime
    PublishedAt: Optional[datetime] = None
    RetiredAt: Optional[datetime] = None


class AgentPromptBulkItem(BaseModel):
    IDResource: UUID
    result: str
    promptID: Optional[UUID] = None
    version: Optional[int] = None
    error: Optional[str] = None


class AgentPromptBulkResponse(BaseModel):
    status: str
    activeAgents: int
    generated: int
    unchanged: int
    failed: int
    items: list[AgentPromptBulkItem]
