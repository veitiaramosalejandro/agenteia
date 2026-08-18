from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID
import psycopg
from psycopg.rows import dict_row

from app.config import settings


def _postgres_connection() -> psycopg.Connection:
    """Abre una conexión corta a la base PostgreSQL de la aplicación."""
    return psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
        row_factory=dict_row,
    )


def save_sys_resource_ia(configuration: dict[str, Any]) -> dict[str, Any]:
    """Crea o actualiza la configuración canónica de un agente por IDResource."""
    values = (
        configuration.get("Name"),
        configuration.get("Stamp"),
        configuration.get("IDResource"),
        configuration.get("active", False),
    )

    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysResourceIA" (
                    "Name", "Stamp", "IDResource", active
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT ("IDResource") DO UPDATE SET
                    "Name" = EXCLUDED."Name",
                    "Stamp" = EXCLUDED."Stamp",
                    active = EXCLUDED.active
                RETURNING *
                ''',
                values,
            )
            saved = cursor.fetchone()

    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió la configuración guardada.")
    return dict(saved)


def get_solidset_login_for_active_agent(resource_id: UUID | str) -> dict[str, Any] | None:
    """Obtiene internamente la cuenta de un agente activo; nunca exponer este resultado por API."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT l."IDLogin", l."Username", l."Password", l."Salt",
                       l."LastIDResource", l."ActiveIDLogin2Resource"
                FROM public."SysLogin" l
                INNER JOIN public."SysResourceIA" r
                    ON (
                        r."ActiveIDLogin2Resource" IS NOT NULL
                        AND l."ActiveIDLogin2Resource" = r."ActiveIDLogin2Resource"
                    ) OR (
                        r."ActiveIDLogin2Resource" IS NULL
                        AND l."LastIDResource" = r."IDResource"
                    )
                WHERE r."IDResource" = %s
                  AND r.active = true
                  AND NULLIF(l."Username", '') IS NOT NULL
                  AND NULLIF(l."Password", '') IS NOT NULL
                ORDER BY l."IDLogin"
                LIMIT 1
                ''',
                (UUID(str(resource_id)),),
            )
            row = cursor.fetchone()
            return dict(row) if row is not None else None


def get_active_agents_for_workroom(
    workroom_id: UUID | str,
    selected_resource_ids: Iterable[UUID | str],
) -> list[dict[str, Any]]:
    """Devuelve únicamente agentes activos, seleccionados y asignados al canal."""
    selected = list(dict.fromkeys(UUID(str(value)) for value in selected_resource_ids))
    if not selected:
        return []
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT r."ID", r."Name", r."IDResource", r.active,
                       c."IDWorkRoom", c.response_order, login."FullName"
                FROM public."SysResourceIA" r
                INNER JOIN public."SysChatIAResource" c
                    ON c."IDResource" = r."IDResource"
                LEFT JOIN LATERAL (
                    SELECT l."FullName"
                    FROM public."SysLogin" l
                    WHERE (
                        r."ActiveIDLogin2Resource" IS NOT NULL
                        AND l."ActiveIDLogin2Resource" = r."ActiveIDLogin2Resource"
                    ) OR (
                        r."ActiveIDLogin2Resource" IS NULL
                        AND l."LastIDResource" = r."IDResource"
                    )
                    ORDER BY
                        CASE WHEN NULLIF(BTRIM(l."FullName"), '') IS NULL THEN 1 ELSE 0 END,
                        l."IDLogin"
                    LIMIT 1
                ) login ON true
                WHERE c."IDWorkRoom" = %s
                  AND r.active = true
                  AND c.active = true
                  AND r."IDResource" = ANY(%s)
                ORDER BY c.response_order ASC, r."Name" ASC, r."IDResource" ASC
                ''',
                (UUID(str(workroom_id)), selected),
            )
            return [dict(row) for row in cursor.fetchall()]


def ensure_payload_agent_workroom_assignments(
    workroom_id: UUID | str,
    resource_ids: Iterable[UUID | str],
) -> int:
    """Vincula al canal recursos activos descubiertos en el payload de SolidSET."""
    resources = list(dict.fromkeys(UUID(str(value)) for value in resource_ids))
    if not resources:
        return 0
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysChatIAResource" (
                    "IDResource", "IDWorkRoom", active, response_order
                )
                SELECT r."IDResource", %s, true, 0
                FROM public."SysResourceIA" r
                WHERE r.active = true
                  AND r."IDResource" = ANY(%s)
                ON CONFLICT ("IDResource", "IDWorkRoom") DO NOTHING
                ''',
                (UUID(str(workroom_id)), resources),
            )
            return max(0, cursor.rowcount)


def save_agent_knowledge(knowledge: dict[str, Any]) -> dict[str, Any]:
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysResourceIAKnowledge" (
                    "IDResource", "IDWorkRoom", "Title", "KnowledgeText", "Source", active
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                ''',
                (
                    knowledge["IDResource"], knowledge.get("IDWorkRoom"),
                    knowledge.get("Title"), knowledge["KnowledgeText"],
                    knowledge.get("Source", "manual"), knowledge.get("active", True),
                ),
            )
            saved = cursor.fetchone()
    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió el conocimiento guardado.")
    return dict(saved)


def get_agent_knowledge(resource_id: UUID | str, workroom_id: UUID | str) -> str:
    """Obtiene conocimiento privado del agente y el específico del canal actual."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "Title", "KnowledgeText", "Source"
                FROM public."SysResourceIAKnowledge"
                WHERE "IDResource" = %s
                  AND active = true
                  AND ("IDWorkRoom" IS NULL OR "IDWorkRoom" = %s)
                ORDER BY "IDWorkRoom" NULLS FIRST, "Stamp" DESC
                LIMIT 30
                ''',
                (UUID(str(resource_id)), UUID(str(workroom_id))),
            )
            rows = cursor.fetchall()
    return "\n\n".join(
        f"[{row.get('Title') or row.get('Source') or 'Conocimiento'}]\n{row['KnowledgeText']}"
        for row in rows
    )[:20000]


def configure_agent_workroom(
    resource_id: UUID | str,
    workroom_id: UUID | str,
    *,
    active: bool,
    response_order: int,
) -> dict[str, Any]:
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysChatIAResource" (
                    "IDResource", "IDWorkRoom", active, response_order
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT ("IDResource", "IDWorkRoom") DO UPDATE SET
                    active = EXCLUDED.active,
                    response_order = EXCLUDED.response_order
                RETURNING *
                ''',
                (UUID(str(resource_id)), UUID(str(workroom_id)), active, response_order),
            )
            saved = cursor.fetchone()
    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió la asignación guardada.")
    return dict(saved)


def touch_agent_session(
    session_id: UUID | str,
    resource_id: UUID | str,
    workroom_id: UUID | str,
    *,
    status: str = "active",
) -> dict[str, Any]:
    """Crea la sesión lógica del agente o actualiza su última actividad."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysAgentIASession" (
                    "IDSession", "IDResource", "IDWorkRoom", "Status"
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT ("IDSession", "IDResource") DO UPDATE SET
                    "IDWorkRoom" = EXCLUDED."IDWorkRoom",
                    "LastActivityAt" = CURRENT_TIMESTAMP,
                    "Status" = EXCLUDED."Status"
                RETURNING *
                ''',
                (
                    UUID(str(session_id)), UUID(str(resource_id)),
                    UUID(str(workroom_id)), status,
                ),
            )
            saved = cursor.fetchone()
    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió la sesión guardada.")
    return dict(saved)
