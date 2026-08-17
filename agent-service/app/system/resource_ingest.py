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


CHAT_RESOURCE_QUERY = """
    SELECT
        SysResources.DisplayName,
        SysResources.ResourceId,
        SysLogin.FullName,
        SysWorkRoom.Code,
        SysWorkRoom.Name,
        SysWorkRoom.IDWorkRoom
    FROM dbo.SysResources
    INNER JOIN dbo.SysLogin
        ON SysLogin.ActiveIDLogin2Resource = SysResources.ActiveIDLogin2Resource
    INNER JOIN dbo.SysWorkRoomResource
        ON SysWorkRoomResource.IDResource = SysResources.ResourceId
    INNER JOIN dbo.SysWorkRoom
        ON SysWorkRoom.IDWorkRoom = SysWorkRoomResource.IDWorkRoom
    ORDER BY SysResources.DisplayName ASC
"""


WORKROOM_QUERY = """
    SELECT Code, Name, Description, IDWorkRoom
    FROM dbo.SysWorkRoom
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


def ingest_solidset_chat_resources() -> dict[str, int]:
    """Sincroniza las relaciones recurso-sala desde SolidSET."""
    with pymssql.connect(
        server=settings.SQL_SERVER_HOST,
        user=settings.SQL_SERVER_USER,
        password=settings.SQL_SERVER_PASSWORD,
        database=settings.SQL_SERVER_DB,
        login_timeout=max(3, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
        timeout=max(10, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
    ) as source_connection:
        source_cursor = source_connection.cursor(as_dict=True)
        source_cursor.execute(CHAT_RESOURCE_QUERY)
        source_rows = source_cursor.fetchall() or []

    relations: dict[tuple[UUID, UUID], str | None] = {}
    skipped = 0
    for row in source_rows:
        try:
            resource_id = UUID(str(row.get("ResourceId")))
            workroom_id = UUID(str(row.get("IDWorkRoom")))
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue
        display_name = row.get("DisplayName")
        relations[(resource_id, workroom_id)] = (
            str(display_name).strip() if display_name is not None else None
        )

    relation_keys = list(relations)
    with _postgres_connection() as target_connection:
        with target_connection.cursor() as target_cursor:
            # Garantiza la clave padre incluso si esta ingesta se ejecuta sola.
            target_cursor.executemany(
                '''
                INSERT INTO public."SysResourceIA" ("Name", "Stamp", "IDResource")
                VALUES (%s, CURRENT_TIMESTAMP, %s)
                ON CONFLICT ("IDResource") DO UPDATE SET
                    "Name" = EXCLUDED."Name",
                    "Stamp" = EXCLUDED."Stamp"
                ''',
                [
                    (display_name, resource_id)
                    for (resource_id, _), display_name in relations.items()
                ],
            )

            existing_keys: set[tuple[UUID, UUID]] = set()
            if relation_keys:
                target_cursor.execute(
                    'SELECT "IDResource", "IDWorkRoom" '
                    'FROM public."SysChatIAResource"'
                )
                requested = set(relation_keys)
                existing_keys = {
                    (row["IDResource"], row["IDWorkRoom"])
                    for row in target_cursor.fetchall()
                    if (row["IDResource"], row["IDWorkRoom"]) in requested
                }
                target_cursor.executemany(
                    '''
                    INSERT INTO public."SysChatIAResource" ("IDResource", "IDWorkRoom")
                    VALUES (%s, %s)
                    ON CONFLICT ("IDResource", "IDWorkRoom") DO NOTHING
                    ''',
                    relation_keys,
                )

    return {
        "sourceRows": len(source_rows),
        "synchronized": len(relations),
        "inserted": len(set(relation_keys) - existing_keys),
        "existing": len(existing_keys),
        "skipped": skipped,
    }


def ingest_solidset_workrooms() -> dict[str, int]:
    """Sincroniza el catálogo de canales de SolidSET hacia PostgreSQL."""
    with pymssql.connect(
        server=settings.SQL_SERVER_HOST,
        user=settings.SQL_SERVER_USER,
        password=settings.SQL_SERVER_PASSWORD,
        database=settings.SQL_SERVER_DB,
        login_timeout=max(3, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
        timeout=max(10, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
    ) as source_connection:
        source_cursor = source_connection.cursor(as_dict=True)
        source_cursor.execute(WORKROOM_QUERY)
        source_rows = source_cursor.fetchall() or []

    workrooms: dict[UUID, tuple[str | None, str | None, str | None]] = {}
    skipped = 0
    for row in source_rows:
        try:
            workroom_id = UUID(str(row.get("IDWorkRoom")))
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue

        def clean(value: object) -> str | None:
            if value is None:
                return None
            normalized = str(value).strip()
            return normalized or None

        workrooms[workroom_id] = (
            clean(row.get("Code")),
            clean(row.get("Name")),
            clean(row.get("Description")),
        )

    workroom_ids = list(workrooms)
    with _postgres_connection() as target_connection:
        with target_connection.cursor() as target_cursor:
            existing_ids: set[UUID] = set()
            if workroom_ids:
                target_cursor.execute(
                    'SELECT "IDWorkRoom" FROM public."SysWorkRoom" '
                    'WHERE "IDWorkRoom" = ANY(%s)',
                    (workroom_ids,),
                )
                existing_ids = {row["IDWorkRoom"] for row in target_cursor.fetchall()}
                target_cursor.executemany(
                    '''
                    INSERT INTO public."SysWorkRoom" (
                        "IDWorkRoom", "Code", "Name", "Description"
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT ("IDWorkRoom") DO UPDATE SET
                        "Code" = EXCLUDED."Code",
                        "Name" = EXCLUDED."Name",
                        "Description" = EXCLUDED."Description"
                    ''',
                    [
                        (workroom_id, code, name, description)
                        for workroom_id, (code, name, description) in workrooms.items()
                    ],
                )

    return {
        "sourceRows": len(source_rows),
        "synchronized": len(workrooms),
        "inserted": len(set(workroom_ids) - existing_ids),
        "updated": len(existing_ids),
        "skipped": skipped,
    }
