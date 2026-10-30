from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _task_json_alias(field_name: str) -> str:
    """Convierte nombres C# PascalCase a camelCase, incluyendo IDTask -> idTask."""

    if field_name.startswith("ID"):
        return "id" + field_name[2:]
    return field_name[:1].lower() + field_name[1:]


class TaskJuntaData(BaseModel):
    """Representación del contrato TaskJuntaData recibido desde SolidSET."""

    model_config = ConfigDict(
        alias_generator=_task_json_alias,
        populate_by_name=True,
        extra="forbid",
    )

    IDTask: UUID
    Code: str | None = Field(default=None, max_length=255)
    ModifiedTime: datetime | None = None
    StartDate: datetime | None = None
    ShortName: str | None = Field(default=None, max_length=500)
    Description: str | None = Field(default=None, max_length=10_000)
    EndDate: datetime | None = None
    DueDate: datetime | None = None
    Duration: int | None = None
    TaskKind: int | None = None
    Status: int | None = None
    Importance: int | None = None
    Complexity: int | None = None
    Priority: int | None = None
    QuestionSelectionMode: int | None = None
    QuestionAllowVoteInt: int | None = None
    QuestionProfile: int | None = None
    QuestionAllowUserOptions: int | None = None
    QuestionPublicVoting: int | None = None
    QuestionJustifMandatory: int | None = None
    EnableQuestion: int | None = None
    LockVoting: int | None = None
    LockQuestionEdit: int | None = None
    RunningStatus: int | None = None
    ProgressPercentage: Decimal | None = None
