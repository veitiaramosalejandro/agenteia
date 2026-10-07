from typing import Optional

from pydantic import BaseModel, Field


class HistoricalIngestionStartRequest(BaseModel):
    instanceCode: Optional[str] = None
    dryRun: bool = True


class SystemKnowledgeIngestionStartRequest(BaseModel):
    instanceCode: str = Field(..., min_length=1, max_length=100)
    tables: Optional[list[str]] = Field(None, max_length=50)

    class Config:
        extra = "forbid"
