from __future__ import annotations

import re

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatConversationRequest(BaseModel):
    session_id: str = Field(..., description="Conversation session ID")
    message: str = Field(..., description="Message submitted by the user")
    user_id: str = Field(..., description="Username of the user making the request")
    resource_id: Optional[str] = Field(
        None, description="Canonical IDResource of the participant"
    )
    login_id: Optional[str] = Field(None, description="IDLogin of the active session")
    canal_id: Optional[str] = Field(None, description="Current workroom ID (optional)")
    generate_audio: bool = Field(
        False, description="Whether an audio response should be generated"
    )


class ChatConversationResponse(BaseModel):
    session_id: str
    user_message: str
    agent_response: str
    audio_url: Optional[str] = None
    user_context_used: Optional[str] = None  # Para debugging


class UserFeedbackRequest(BaseModel):
    session_id: str = Field(..., description="Conversation session ID")
    user_id: str = Field(..., description="Username of the user providing feedback")
    user_text: str = Field(..., description="Original user message")
    agent_response: str = Field(..., description="Agent response being evaluated")
    corrected_response: Optional[str] = Field(
        None, description="Expected response or user correction"
    )
    canal_id: Optional[str] = Field(
        None, description="Workroom ID where the interaction occurred"
    )
    feedback_type: str = Field(
        "explicit", description="Feedback type: explicit or implicit"
    )
    reason: Optional[str] = Field(
        None, description="Reason for the feedback or correction"
    )
    previous_user_text: Optional[str] = Field(
        None, description="Previous user message used to detect repetition"
    )
    update_profile: bool = Field(
        True, description="Whether the dynamic user profile should be updated"
    )


class UserFeedbackResponse(BaseModel):
    status: str
    learned: bool
    profile_updated: bool
    reaction_signal: str
    topics: list[str] = []


class SolidSETReactionCaptureRequest(BaseModel):
    IDChat: int = Field(..., gt=0)
    IDUser: uuid.UUID
    IDChannel: uuid.UUID
    IDEmoji: str = Field(..., min_length=1, max_length=64)
    Counter: int = Field(..., ge=0)

    class Config:
        extra = "forbid"


class SolidSETReactionCaptureResponse(BaseModel):
    status: str
    learned: bool
    changed: bool
    signal: str
    reward: float
    IDChat: int
    IDAgentResource: uuid.UUID
    AgentName: str


class SysResourceIAConfiguration(BaseModel):
    Name: Optional[str] = Field(None, max_length=255)
    Stamp: Optional[datetime] = None
    IDResource: uuid.UUID
    IDAgentResource: Optional[uuid.UUID] = None
    active: bool = False

    class Config:
        extra = "forbid"


class SysResourceIAConfigurationStored(SysResourceIAConfiguration):
    ID: uuid.UUID


class SysResourceIAConfigurationResponse(BaseModel):
    status: str
    configuration: SysResourceIAConfigurationStored


class SolidSETDataAPIConfiguration(BaseModel):
    BaseUrl: str = Field(..., min_length=8, max_length=500)
    APIKey: Optional[str] = Field(None, max_length=8000)
    TimeoutSeconds: int = Field(120, ge=5, le=3600)
    MaxRows: int = Field(5000, ge=1, le=100000)
    VerifyTLS: bool = True
    active: bool = True

    class Config:
        extra = "forbid"


class SolidSETDataAPIStored(BaseModel):
    BaseUrl: str
    TimeoutSeconds: int
    MaxRows: int
    VerifyTLS: bool
    active: bool
    APIKeyConfigured: bool = True


class SolidSETInstanceConfiguration(BaseModel):
    Code: str = Field(..., min_length=1, max_length=80)
    Name: str = Field(..., min_length=1, max_length=255)
    BaseUrl: str = Field(..., min_length=8, max_length=500)
    NotificationUrl: Optional[str] = Field(None, max_length=500)
    SourceIP: Optional[str] = Field(None, max_length=255)
    CountryCode: str = Field("PT", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    Locale: str = Field(
        "pt-PT",
        min_length=2,
        max_length=20,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",
    )
    TimeZone: str = Field("Europe/Lisbon", min_length=1, max_length=80)
    active: bool = True
    DataAPI: Optional[SolidSETDataAPIConfiguration] = None

    class Config:
        extra = "forbid"


class SolidSETInstanceStored(SolidSETInstanceConfiguration):
    ID: uuid.UUID
    CreatedAt: datetime
    UpdatedAt: datetime

    DataAPI: Optional[SolidSETDataAPIStored] = None


class SolidSETInstanceConfigurationResponse(BaseModel):
    status: str
    configuration: SolidSETInstanceStored


class SolidSETInstanceListResponse(BaseModel):
    total: int
    items: list[SolidSETInstanceStored]


class SolidSETDataAPIConnectionTestResponse(BaseModel):
    status: str
    instanceCode: str
    connected: bool
    databaseName: Optional[str] = None
    serverVersion: Optional[str] = None
    adapterCode: str
    hasSysResource2Agent: bool


class MultiAgentDialogueRequest(BaseModel):
    IDWorkRoom: uuid.UUID
    IDSession: Optional[uuid.UUID] = None
    RawMessage: str = Field(..., min_length=1, max_length=5000)
    SelectedAgentResourceIds: list[uuid.UUID]
    SenderResourceId: Optional[uuid.UUID] = None
    SendToSolidSET: bool = False
    SolidSETInstanceCode: Optional[str] = Field(None, max_length=80)

    class Config:
        extra = "forbid"


class MultiAgentAnswer(BaseModel):
    IDAgentResource: uuid.UUID
    AgentName: str
    response: str
    sent: bool = False
    sendDetail: Optional[str] = None


class MultiAgentDialogueResponse(BaseModel):
    IDSession: uuid.UUID
    IDWorkRoom: uuid.UUID
    responses: list[MultiAgentAnswer]


class AgentKnowledgeRequest(BaseModel):
    IDWorkRoom: Optional[uuid.UUID] = None
    Title: Optional[str] = Field(None, max_length=255)
    KnowledgeText: str = Field(..., min_length=1, max_length=50000)
    Source: str = Field("manual", min_length=1, max_length=100)
    active: bool = True

    class Config:
        extra = "forbid"


class AgentKnowledgeResponse(BaseModel):
    ID: uuid.UUID
    IDResource: uuid.UUID
    IDWorkRoom: Optional[uuid.UUID] = None
    Title: Optional[str] = None
    KnowledgeText: str
    Source: str
    Stamp: datetime
    active: bool
    indexed: bool


class AgentWorkRoomConfiguration(BaseModel):
    active: bool = True
    response_order: int = Field(0, ge=0, le=1000)

    class Config:
        extra = "forbid"


class AgentWorkRoomConfigurationResponse(BaseModel):
    IDResource: uuid.UUID
    IDWorkRoom: uuid.UUID
    active: bool
    response_order: int


def _to_camel_alias(field_name: str) -> str:
    """Convierte PascalCase a camelCase respetando prefijos como ID."""
    acronym = re.match(r"^[A-Z]+(?=[A-Z][a-z]|$)", field_name)
    if acronym:
        prefix = acronym.group(0)
        return prefix.lower() + field_name[len(prefix) :]
    return field_name[:1].lower() + field_name[1:]


class FrameworkMessageDTO(BaseModel):
    """Contrato receptor compatible con el DTO FrameworkMessage de Notification."""

    Stamp: Optional[datetime] = None
    Sender: Optional[dict[str, Any]] = None
    Destiny: Optional[dict[str, Any]] = None
    ExternalDestinations: Optional[list[dict[str, Any]]] = None
    ExcludeSenderUser: bool = False
    ExcludeSenderSession: bool = False
    IncludeSenderSession: bool = False
    Kind: Any = None
    IDNotification: Optional[str] = None
    RawMessage: Optional[str] = None
    RawMessageHtml: Optional[str] = None
    Importance: Any = None
    Priority: int = 0
    Modifiers: int = 0
    VisibilityLevel: Any = None
    MaskMessage: int = 0
    MessageMonitoring: int = 0
    Args: Optional[list[Any]] = None
    PointData: Any = None
    Chat: Any = None
    UserData: Any = None
    ChatReadData: Optional[list[Any]] = None
    ImportanceSettingData: Any = None
    NotificationSettingsData: Any = None
    MailData: Any = None
    CompanyData: Any = None
    VideoCallData: Any = None
    MeetingData: Any = None
    TaskData: Any = None
    ActivityData: Any = None
    Task: Any = None
    ScheduleActivity: Any = None
    ChatData: Any = None
    ChatTransferingData: Any = None
    ScheduledData: Any = None
    WorkRoomData: Any = None
    RecordData: Any = None
    ObjectContent: Any = None
    IDChatExtVars: Optional[str] = None
    Info: Optional[dict[str, str]] = None
    ExtraData: Optional[str] = None
    LinkData: Any = None
    TimeData: Any = None
    FeatureFlagData: Any = None
    RelatedRecordsData: Optional[list[Any]] = None
    ReminderData: Any = None
    AttentionCallNotificationLevel: Any = None
    AttentionCallNotify: bool = False
    NotifyDate: Optional[datetime] = None
    DebugData: Optional[list[Any]] = None
    TreatLaterNotifData: Any = None

    class Config:
        extra = "allow"
        populate_by_name = True
        alias_generator = _to_camel_alias


class SendMessageResultDTO(BaseModel):
    Result: int
    Message: FrameworkMessageDTO
    Error: Optional[str] = None
    requestId: Optional[str] = None
    status: Optional[str] = None
    statusUrl: Optional[str] = None


class ChatQuestionSuggestionItem(BaseModel):
    id: str
    text: str


class ChatQuestionSuggestionResponse(BaseModel):
    requestId: str
    questionChatId: str
    status: str
    code: int
    language: str
    title: Optional[str] = None
    suggestions: list[ChatQuestionSuggestionItem]
    statusUrl: str
