from pydantic import BaseModel, Field


class ModelValidationRequest(BaseModel):
    capabilities: list[str] = Field(
        default_factory=lambda: ["general", "structured_json", "tool_calling", "vision", "audio"],
        min_length=1,
    )


class ModelCapabilityResult(BaseModel):
    capability: str
    ok: bool
    status: str
    detail: str = ""
    latencyMs: float | None = None


class ModelValidationResponse(BaseModel):
    provider: str
    model: str
    modelType: str = "unknown"
    declaredCapabilities: list[str] = Field(default_factory=list)
    embeddingDimension: int | None = None
    available: bool
    availableDetail: str
    capabilities: list[ModelCapabilityResult]
    passed: bool
