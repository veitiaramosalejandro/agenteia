from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pymssql

from app.config import settings
from app.connectors.db_client import _postgres_connection


RESOURCE_QUERY = """
    SELECT
        SysResources.DisplayName,
        SysResources.ResourceId,
        SysLogin.FullName
    FROM dbo.SysResources
    INNER JOIN dbo.SysLogin
        ON SysLogin.ActiveIDLogin2Resource = SysResources.ActiveIDLogin2Resource
    ORDER BY SysResources.DisplayName ASC
"""


def ingest_solidset_resources() -> dict[str, int]:
    """Sincroniza los recursos de SolidSET desde SQL Server hacia PostgreSQL."""
    with pymssql.connect(
        server=settings.SQL_SERVER_HOST,
        user=settings.SQL_SERVER_USER,
        password=settings.SQL_SERVER_PASSWORD,
        database=settings.SQL_SERVER_DB,
        login_timeout=max(3, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
        timeout=max(10, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
    ) as source_connection:
        source_cursor = source_connection.cursor(as_dict=True)
        source_cursor.execute(RESOURCE_QUERY)
        source_rows = source_cursor.fetchall() or []

    # El diccionario evita duplicados si SQL Server devuelve más de un login
    # para el mismo recurso. La clave canónica siempre es ResourceId.
    resources: dict[UUID, str | None] = {}
    skipped = 0
    for row in source_rows:
        raw_resource_id = row.get("ResourceId")
        try:
            resource_id = UUID(str(raw_resource_id))
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue
        display_name = row.get("DisplayName")
        resources[resource_id] = str(display_name).strip() if display_name is not None else None

    resource_ids = list(resources)
    synchronized_at = datetime.now()
    with _postgres_connection() as target_connection:
        with target_connection.cursor() as target_cursor:
            existing_ids: set[UUID] = set()
            if resource_ids:
                target_cursor.execute(
                    'SELECT "IDResource" FROM public."SysResourceIA" '
                    'WHERE "IDResource" = ANY(%s)',
                    (resource_ids,),
                )
                existing_ids = {row["IDResource"] for row in target_cursor.fetchall()}

                target_cursor.executemany(
                    '''
                    INSERT INTO public."SysResourceIA" ("Name", "Stamp", "IDResource")
                    VALUES (%s, %s, %s)
                    ON CONFLICT ("IDResource") DO UPDATE SET
                        "Name" = EXCLUDED."Name",
                        "Stamp" = EXCLUDED."Stamp"
                    ''',
                    [
                        (display_name, synchronized_at, resource_id)
                        for resource_id, display_name in resources.items()
                    ],
                )

    inserted = len(set(resource_ids) - existing_ids)
    updated = len(existing_ids)
    return {
        "sourceRows": len(source_rows),
        "synchronized": len(resources),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }
