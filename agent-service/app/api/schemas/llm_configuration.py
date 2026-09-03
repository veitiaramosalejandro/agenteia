from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LLMProviderConfiguration(BaseModel):
    Code: str = Field(..., min_length=1, max_length=80)
    Name: str = Field(..., min_length=1, max_length=255)
    Provider: str = Field(..., min_length=1, max_length=40)
    Model: str = Field(..., min_length=1, max_length=255)
    BaseUrl: Optional[str] = Field(None, max_length=500)
    APIKey: Optional[str] = Field(None, max_length=8000)
    Temperature: float = Field(0.5, ge=0, le=2)
    MaxOutputTokens: int = Field(1024, gt=0, le=131072)
    TimeoutSeconds: int = Field(60, gt=0, le=3600)
    AzureEndpoint: Optional[str] = Field(None, max_length=500)
    AzureApiVersion: Optional[str] = Field(None, max_length=80)
    AzureDeployment: Optional[str] = Field(None, max_length=255)
    IDResource: Optional[UUID] = None
    IsDefault: bool = False
    active: bool = True

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
    IDResource: Optional[UUID] = None
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
