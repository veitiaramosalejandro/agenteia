from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.agent.capabilities import normalize_capabilities


class WorkRoomAgentConfiguration(BaseModel):
    active: bool = True
    response_order: int = Field(0, ge=0, le=1000)

    class Config:
        extra = "forbid"


class AutomationRuleRequest(BaseModel):
    IDResource: UUID
    IDWorkRoom: UUID
    Name: str = Field(..., min_length=1, max_length=160)
    TriggerType: Literal["manual", "selected_message"] = "manual"
    Instruction: str = Field("", max_length=5000)
    RequiredCapabilities: list[str] = Field(default_factory=list, max_length=30)
    MaxRunsPerHour: int = Field(10, ge=1, le=1000)
    RequireApproval: bool = True
    active: bool = True

    @model_validator(mode="after")
    def normalize_required_capabilities(self):
        self.RequiredCapabilities = sorted(normalize_capabilities(self.RequiredCapabilities))
        return self

    class Config:
        extra = "forbid"


class AutomationEvaluationRequest(BaseModel):
    Message: str = Field(..., min_length=1, max_length=5000)
    Approved: bool = False

    class Config:
        extra = "forbid"


class AutomationExecutionRequest(AutomationEvaluationRequest):
    SenderResourceId: UUID | None = None
    SendToSolidSET: bool = False
