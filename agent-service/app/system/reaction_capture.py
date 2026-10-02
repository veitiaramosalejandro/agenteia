from __future__ import annotations

from typing import Any
from uuid import UUID

import pymssql

from app.config import settings
from app.connectors.db_client import _postgres_connection
from app.connectors.solidset_sql import connect as connect_solidset_sql


def classify_reaction(emoji: str, counter: int) -> str:
    if counter <= 0:
        return "removed"
    normalized = (emoji or "").strip().upper()
    positive = {
        "U+1F44C", "U+1F64F", "U+1F44D", "U+1F499", "U+1F4A1","U+1F44F", "U+1F4AA", "U+2705", 
        "👌", "🙏", "👍", "💙", "💡", "👏", "💪", "✅",
    }
    negative = {
        "U+1F44E", "U+1F6E0", "U+1F4DD", "U+1F6AB",
        "👎", "🛠️", "📝", "🚫",
    }
    if normalized in positive:
        return "positive"
    if normalized in negative:
        return "negative"
    return "neutral"


def reaction_reward(signal: str, counter: int) -> float:
    magnitude = max(0, int(counter))
    if signal == "positive":
        return float(magnitude)
    if signal == "negative":
        return float(-magnitude)
    if signal == "neutral":
        return round(0.1 * magnitude, 2)
    return 0.0


def resolve_agent_message(id_chat: int, instance: dict[str, Any]) -> dict[str, Any] | None:
    """Resuelve el mensaje original en SQL Server y valida su agente en PostgreSQL."""
    with connect_solidset_sql(instance, as_dict=True) as connection:
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
    if not message:
        return None

    try:
        resource_id = UUID(str(message.get("IDSenderResource")))
    except (TypeError, ValueError, AttributeError):
        return None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT
                    r."IDResource",
                    r."IDAgentResource",
                    r."Name",
                    l."FullName"
                FROM public."SysResourceIA" r
                LEFT JOIN public."SysLogin" l
                  ON l."ActiveIDLogin2Resource" = r."ActiveIDLogin2Resource"
                WHERE r.active = TRUE
                  AND (
                      r."IDResource" = %s
                      OR r."IDAgentResource" = %s
                  )
                ORDER BY
                    CASE WHEN r."IDResource" = %s THEN 0 ELSE 1 END,
                    l."IDLogin"
                LIMIT 1
                ''',
                (resource_id, resource_id, resource_id),
            )
            agent_row = cursor.fetchone()
    if agent_row is None:
        return None
    resolved_agent = agent_row.get("IDAgentResource") or agent_row.get("IDResource")
    return {**message, **dict(agent_row), "IDAgentResource": resolved_agent}


def save_agent_reaction(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Persiste la reacción de forma idempotente y devuelve si cambió."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "Counter", "Signal", "Reward"
                FROM public."SysAgentIAReaction"
                WHERE "IDChat" = %s AND "IDUser" = %s AND "IDEmoji" = %s
                ''',
                (data["IDChat"], data["IDUser"], data["IDEmoji"]),
            )
            previous = cursor.fetchone()
            changed = previous is None or (
                previous["Counter"] != data["Counter"]
                or previous["Signal"] != data["Signal"]
                or float(previous["Reward"]) != float(data["Reward"])
            )
            cursor.execute(
                '''
                INSERT INTO public."SysAgentIAReaction" (
                    "IDChat", "IDUser", "IDChannel", "IDEmoji", "Counter",
                    "Signal", "Reward", "IDAgentResource", "AgentResponse"
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ("IDChat", "IDUser", "IDEmoji") DO UPDATE SET
                    "IDChannel" = EXCLUDED."IDChannel",
                    "Counter" = EXCLUDED."Counter",
                    "Signal" = EXCLUDED."Signal",
                    "Reward" = EXCLUDED."Reward",
                    "IDAgentResource" = EXCLUDED."IDAgentResource",
                    "AgentResponse" = EXCLUDED."AgentResponse",
                    "UpdatedAt" = CURRENT_TIMESTAMP
                RETURNING *
                ''',
                (
                    data["IDChat"], data["IDUser"], data["IDChannel"],
                    data["IDEmoji"], data["Counter"], data["Signal"],
                    data["Reward"], data["IDAgentResource"], data["AgentResponse"],
                ),
            )
            saved = cursor.fetchone()
    return dict(saved), changed


def get_agent_reinforcement_context(
    resource_id: UUID | str,
    channel_id: UUID | str,
    limit: int = 3,
) -> str:
    """Construye la política de preferencias aprendida para futuras respuestas."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "AgentResponse", SUM("Reward") AS reward,
                       MAX("UpdatedAt") AS last_update
                FROM public."SysAgentIAReaction" reaction
                INNER JOIN public."SysResourceIA" resource
                  ON resource."IDResource" = reaction."IDAgentResource"
                WHERE (
                    resource."IDResource" = %s
                    OR resource."IDAgentResource" = %s
                )
                  AND reaction."IDChannel" = %s
                  AND reaction."Counter" > 0
                GROUP BY reaction."IDChat", reaction."AgentResponse"
                HAVING SUM(reaction."Reward") <> 0
                ORDER BY ABS(SUM(reaction."Reward")) DESC,
                         MAX(reaction."UpdatedAt") DESC
                LIMIT %s
                ''',
                (
                    UUID(str(resource_id)), UUID(str(resource_id)),
                    UUID(str(channel_id)), max(1, limit * 2),
                ),
            )
            rows = cursor.fetchall()
    positive = [row for row in rows if float(row["reward"]) > 0][:limit]
    negative = [row for row in rows if float(row["reward"]) < 0][:limit]
    blocks: list[str] = []
    if positive:
        blocks.append("Patrones valorados positivamente; favorece su claridad y enfoque:")
        blocks.extend(
            f"+ recompensa {float(row['reward']):g}: {str(row['AgentResponse'])[:500]}"
            for row in positive
        )
    if negative:
        blocks.append("Patrones penalizados; evita repetir sus errores o estilo:")
        blocks.extend(
            f"- recompensa {float(row['reward']):g}: {str(row['AgentResponse'])[:500]}"
            for row in negative
        )
    return "\n".join(blocks)
