"""Reglas puras de participantes y visibilidad para mensajes SolidSET."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterable
from uuid import UUID


ZERO_UUID = str(UUID(int=0))


class VisibilityLevel(IntEnum):
    PUBLIC = 0
    NORMAL = 1
    CONFIDENTIAL = 2
    PRIVATE = 3


def _lowered(value: Any) -> dict[str, Any]:
    return {str(key).casefold(): item for key, item in value.items()} if isinstance(value, dict) else {}


def normalize_uuid(value: Any) -> str:
    try:
        normalized = str(UUID(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        return ""
    return "" if normalized == ZERO_UUID else normalized


def normalize_bool(value: Any) -> bool:
    return value is True or (
        not isinstance(value, bool)
        and str(value).strip().casefold() in {"1", "true", "yes", "si", "sí", "sim"}
    )


def normalize_visibility(value: Any) -> VisibilityLevel:
    names = {
        "public": VisibilityLevel.PUBLIC,
        "normal": VisibilityLevel.NORMAL,
        "confidential": VisibilityLevel.CONFIDENTIAL,
        "confidencial": VisibilityLevel.CONFIDENTIAL,
        "private": VisibilityLevel.PRIVATE,
        "privado": VisibilityLevel.PRIVATE,
    }
    if isinstance(value, str) and value.strip().casefold() in names:
        return names[value.strip().casefold()]
    try:
        return VisibilityLevel(int(value))
    except (ValueError, TypeError):
        return VisibilityLevel.NORMAL


@dataclass(frozen=True)
class MessageParticipants:
    sender_resource_id: str
    agent_recipient_ids: tuple[str, ...]
    valid: bool
    reason: str


def resolve_resource_table(resource_table: Any) -> MessageParticipants:
    """Resuelve la regla canónica: sender seq=0/false; agente seq!=0/true."""
    if not isinstance(resource_table, list):
        return MessageParticipants("", (), False, "resource_table_missing")
    senders: list[str] = []
    recipients: list[tuple[int, str]] = []
    for row in resource_table:
        item = _lowered(row)
        resource_id = normalize_uuid(item.get("idresource") or item.get("resource"))
        if not resource_id:
            continue
        try:
            sequence = int(item.get("sequence"))
        except (ValueError, TypeError):
            continue
        talks_with_agent = normalize_bool(item.get("talkwithagent"))
        if sequence == 0 and not talks_with_agent:
            senders.append(resource_id)
        elif sequence != 0 and talks_with_agent:
            recipients.append((sequence, resource_id))
    unique_senders = tuple(dict.fromkeys(senders))
    recipients.sort(key=lambda pair: pair[0])
    unique_recipients = tuple(dict.fromkeys(resource for _, resource in recipients))
    if len(unique_senders) != 1:
        reason = "sender_missing" if not unique_senders else "multiple_senders"
        return MessageParticipants("", unique_recipients, False, reason)
    return MessageParticipants(unique_senders[0], unique_recipients, True, "resource_table")


def can_agent_access_message(
    *,
    visibility_level: Any,
    agent_resource_id: Any,
    participants: MessageParticipants,
    belongs_to_channel: bool,
    resource_access_type: Any = None,
) -> tuple[bool, str]:
    """Evalúa acceso sin confiar en instrucciones del prompt o del modelo."""
    agent_id = normalize_uuid(agent_resource_id)
    if not agent_id:
        return False, "invalid_agent_resource"
    visibility = normalize_visibility(visibility_level)
    if visibility is VisibilityLevel.PUBLIC:
        return True, "public_same_instance"
    if visibility is VisibilityLevel.NORMAL:
        return (True, "normal_channel_member") if belongs_to_channel else (False, "not_channel_member")
    if visibility is VisibilityLevel.CONFIDENTIAL:
        try:
            confidential_access = int(resource_access_type) >= int(VisibilityLevel.CONFIDENTIAL)
        except (ValueError, TypeError):
            confidential_access = False
        return (
            (True, "confidential_channel_access")
            if belongs_to_channel and confidential_access
            else (False, "confidential_access_required")
        )
    if not participants.valid:
        return False, participants.reason
    if agent_id == participants.sender_resource_id or agent_id in participants.agent_recipient_ids:
        return True, "private_explicit_participant"
    return False, "private_not_participant"


def selected_agent_recipients(resource_tables: Iterable[Any]) -> list[str]:
    """Combina destinatarios válidos conservando el orden de secuencia."""
    selected: list[str] = []
    for table in resource_tables:
        participants = resolve_resource_table(table)
        if participants.valid:
            selected.extend(participants.agent_recipient_ids)
    return list(dict.fromkeys(selected))
