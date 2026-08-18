from __future__ import annotations

from typing import Any
from uuid import UUID

import pymssql

from app.config import settings
from app.connectors.db_client import _postgres_connection


def classify_reaction(emoji: str, counter: int) -> str:
    if counter <= 0:
        return "removed"
    normalized = (emoji or "").strip().upper()
    positive = {
        "U+1F44D", "U+1F44C", "U+1F64F", "U+2764", "U+2764-FE0F",
        "U+1F499", "U+1F49A", "U+1F49B", "U+1F49C", "U+1F60A",
        "U+1F603", "U+1F604", "U+1F389", "U+1F525", "👍", "👌", "🙏", "❤️",
    }
    negative = {
        "U+1F44E", "U+1F620", "U+1F621", "U+1F612", "U+1F61E",
        "U+1F622", "U+1F641", "👎", "😠", "😡", "😞", "😢",
    }
    if normalized in positive:
        return "positive"
    if normalized in negative:
        return "negative"
    return "neutral"


def resolve_agent_message(id_chat: int) -> dict[str, Any] | None:
    """Resuelve el mensaje original en SQL Server y valida su agente en PostgreSQL."""
    with pymssql.connect(
        server=settings.SQL_SERVER_HOST,
        user=settings.SQL_SERVER_USER,
        password=settings.SQL_SERVER_PASSWORD,
        database=settings.SQL_SERVER_DB,
        login_timeout=max(3, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
        timeout=max(10, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
    ) as connection:
        cursor = connection.cursor(as_dict=True)
        cursor.execute(
            '''
            SELECT TOP 1
                c.IDChat2,
                c.RawMessage,
                c.IDSenderResource,
                c.IDWorkRoom,
                c.Stamp
            FROM dbo.SysChat c WITH (NOLOCK)
            WHERE c.IDChat2 = %s
            ''',
            (id_chat,),
        )
        message = cursor.fetchone()
    if not message or not str(message.get("RawMessage") or "").strip().lower().startswith("asistente ia "):
        return None

    try:
        resource_id = UUID(str(message.get("IDSenderResource")))
    except (TypeError, ValueError, AttributeError):
        return None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT r."IDResource", r."Name", l."FullName"
                FROM public."SysResourceIA" r
                LEFT JOIN public."SysLogin" l
                  ON l."ActiveIDLogin2Resource" = r."ActiveIDLogin2Resource"
                WHERE r."IDResource" = %s
                ORDER BY l."IDLogin"
                LIMIT 1
                ''',
                (resource_id,),
            )
            agent_row = cursor.fetchone()
    if agent_row is None:
        return None
    return {**message, **dict(agent_row), "IDAgentResource": resource_id}


def save_agent_reaction(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Persiste la reacción de forma idempotente y devuelve si cambió."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "Counter", "Signal"
                FROM public."SysAgentIAReaction"
                WHERE "IDChat" = %s AND "IDUser" = %s AND "IDEmoji" = %s
                ''',
                (data["IDChat"], data["IDUser"], data["IDEmoji"]),
            )
            previous = cursor.fetchone()
            changed = previous is None or (
                previous["Counter"] != data["Counter"]
                or previous["Signal"] != data["Signal"]
            )
            cursor.execute(
                '''
                INSERT INTO public."SysAgentIAReaction" (
                    "IDChat", "IDUser", "IDChannel", "IDEmoji", "Counter",
                    "Signal", "IDAgentResource", "AgentResponse"
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ("IDChat", "IDUser", "IDEmoji") DO UPDATE SET
                    "IDChannel" = EXCLUDED."IDChannel",
                    "Counter" = EXCLUDED."Counter",
                    "Signal" = EXCLUDED."Signal",
                    "IDAgentResource" = EXCLUDED."IDAgentResource",
                    "AgentResponse" = EXCLUDED."AgentResponse",
                    "UpdatedAt" = CURRENT_TIMESTAMP
                RETURNING *
                ''',
                (
                    data["IDChat"], data["IDUser"], data["IDChannel"],
                    data["IDEmoji"], data["Counter"], data["Signal"],
                    data["IDAgentResource"], data["AgentResponse"],
                ),
            )
            saved = cursor.fetchone()
    return dict(saved), changed
