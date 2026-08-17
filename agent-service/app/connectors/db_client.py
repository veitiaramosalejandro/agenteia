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
                       c."IDWorkRoom", c."IDSession"
                FROM public."SysResourceIA" r
                INNER JOIN public."SysChatIAResource" c
                    ON c."IDResource" = r."IDResource"
                WHERE c."IDWorkRoom" = %s
                  AND r.active = true
                  AND r."IDResource" = ANY(%s)
                ORDER BY r."Name" ASC, r."IDResource" ASC
                ''',
                (UUID(str(workroom_id)), selected),
            )
            return [dict(row) for row in cursor.fetchall()]
