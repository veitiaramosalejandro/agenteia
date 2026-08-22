from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.connectors.solidset_data_api import (
    read_active_resource_agent,
    read_dataset,
)
from app.connectors.db_client import _postgres_connection


def verify_and_sync_solidset_agent_mapping(
    human_resource_id: UUID | str,
    expected_agent_resource_id: UUID | str | None = None,
    instance: dict[str, object] | None = None,
) -> dict[str, object]:
    """Verifica en SQL Server la relación IA activa y actualiza su réplica local."""
    human_id = UUID(str(human_resource_id))
    expected_id = (
        UUID(str(expected_agent_resource_id))
        if expected_agent_resource_id
        else None
    )
    if instance is None:
        raise RuntimeError("A instância SolidSET é obrigatória para verificar o agente.")
    source_row = read_active_resource_agent(
        instance.get("DataAPI") or {}, str(human_id)
    )

    verified_agent_id = (
        UUID(str(source_row.get("IDAgentResource")))
        if source_row and source_row.get("IDAgentResource")
        else None
    )
    with _postgres_connection() as target_connection:
        with target_connection.cursor() as target_cursor:
            target_cursor.execute(
                '''
                UPDATE public."SysResourceIA"
                SET "IDAgentResource" = %s,
                    active = %s,
                    "Stamp" = %s
                WHERE "IDResource" = %s
                ''',
                (verified_agent_id, bool(verified_agent_id), datetime.now(), human_id),
            )
            local_agent_exists = target_cursor.rowcount == 1

    return {
        "verified": bool(local_agent_exists and verified_agent_id),
        "matchesExpected": bool(
            verified_agent_id and (
                expected_id is None or verified_agent_id == expected_id
            )
        ),
        "IDHumanResource": human_id,
        "IDAgentResource": verified_agent_id,
        "changed": expected_id != verified_agent_id,
    }


def ingest_solidset_logins(instance: dict[str, object]) -> dict[str, int]:
    """Sincroniza cuentas SolidSET para autenticar al agente de cada recurso."""
    source_rows = read_dataset(instance.get("DataAPI") or {}, "logins")

    logins: dict[
        UUID,
        tuple[str | None, str | None, str | None, str | None, UUID | None, UUID | None],
    ] = {}
    skipped = 0

    def optional_uuid(value: object) -> UUID | None:
        if value is None or not str(value).strip():
            return None
        return UUID(str(value))

    for row in source_rows:
        try:
            login_id = UUID(str(row.get("IDLogin")))
            last_resource_id = optional_uuid(row.get("LastIDResource"))
            active_link_id = optional_uuid(row.get("ActiveIDLogin2Resource"))
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue

        username = row.get("Username")
        full_name = row.get("FullName")
        password = row.get("Password")
        salt = row.get("Salt")
        logins[login_id] = (
            str(username).strip() if username is not None else None,
            str(full_name).strip() if full_name is not None else None,
            str(password) if password is not None else None,
            str(salt) if salt is not None else None,
            last_resource_id,
            active_link_id,
        )

    login_ids = list(logins)
    with _postgres_connection() as target_connection:
        with target_connection.cursor() as target_cursor:
            existing_ids: set[UUID] = set()
            if login_ids:
                target_cursor.execute(
                    'SELECT "IDLogin" FROM public."SysLogin" WHERE "IDLogin" = ANY(%s)',
                    (login_ids,),
                )
                existing_ids = {row["IDLogin"] for row in target_cursor.fetchall()}
                target_cursor.executemany(
                    '''
                    INSERT INTO public."SysLogin" (
                        "IDLogin", "Username", "FullName", "Password", "Salt",
                        "LastIDResource", "ActiveIDLogin2Resource"
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ("IDLogin") DO UPDATE SET
                        "Username" = EXCLUDED."Username",
                        "FullName" = EXCLUDED."FullName",
                        "Password" = EXCLUDED."Password",
                        "Salt" = EXCLUDED."Salt",
                        "LastIDResource" = EXCLUDED."LastIDResource",
                        "ActiveIDLogin2Resource" = EXCLUDED."ActiveIDLogin2Resource"
                    ''',
                    [
                        (
                            login_id, username, full_name, password, salt,
                            last_resource_id, active_link_id,
                        )
                        for login_id, (
                            username, full_name, password, salt,
                            last_resource_id, active_link_id,
                        ) in logins.items()
                    ],
                )
                target_cursor.executemany(
                    '''INSERT INTO public."SysSolidSETInstanceLogin" (
                      "IDSolidSETInstance", "IDLogin", "Username", "FullName", "Password", "Salt",
                      "LastIDResource", "ActiveIDLogin2Resource"
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT ("IDSolidSETInstance", "IDLogin") DO UPDATE SET
                      "Username"=EXCLUDED."Username", "FullName"=EXCLUDED."FullName",
                      "Password"=EXCLUDED."Password", "Salt"=EXCLUDED."Salt",
                      "LastIDResource"=EXCLUDED."LastIDResource",
                      "ActiveIDLogin2Resource"=EXCLUDED."ActiveIDLogin2Resource"''',
                    [
                      (instance["ID"], login_id, username, full_name, password, salt,
                       last_resource_id, active_link_id)
                      for login_id, (username, full_name, password, salt,
                                     last_resource_id, active_link_id) in logins.items()
                    ],
                )

    return {
        "sourceRows": len(source_rows),
        "synchronized": len(logins),
        "inserted": len(set(login_ids) - existing_ids),
        "updated": len(existing_ids),
        "skipped": skipped,
    }


def ingest_solidset_resources(instance: dict[str, object]) -> dict[str, int]:
    """Sincroniza los recursos de SolidSET desde SQL Server hacia PostgreSQL."""
    source_rows = read_dataset(instance.get("DataAPI") or {}, "resources")

    # El diccionario evita duplicados si SQL Server devuelve más de un login
    # para el mismo recurso. La clave canónica siempre es ResourceId.
    resources: dict[UUID, tuple[str | None, UUID | None, UUID | None]] = {}
    skipped = 0
    for row in source_rows:
        raw_resource_id = row.get("ResourceId")
        try:
            resource_id = UUID(str(raw_resource_id))
            raw_active_link = row.get("ActiveIDLogin2Resource")
            active_link_id = UUID(str(raw_active_link)) if raw_active_link else None
            raw_agent_resource = row.get("IDAgentResource")
            agent_resource_id = UUID(str(raw_agent_resource)) if raw_agent_resource else None
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue
        display_name = row.get("DisplayName")
        resources[resource_id] = (
            str(display_name).strip() if display_name is not None else None,
            active_link_id,
            agent_resource_id,
        )

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
                    INSERT INTO public."SysResourceIA" (
                        "Name", "Stamp", "IDResource", "ActiveIDLogin2Resource", "IDAgentResource"
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT ("IDResource") DO UPDATE SET
                        "Name" = EXCLUDED."Name",
                        "Stamp" = EXCLUDED."Stamp",
                        "ActiveIDLogin2Resource" = EXCLUDED."ActiveIDLogin2Resource",
                        "IDAgentResource" = EXCLUDED."IDAgentResource"
                    ''',
                    [
                        (display_name, synchronized_at, resource_id, active_link_id, agent_resource_id)
                        for resource_id, (display_name, active_link_id, agent_resource_id) in resources.items()
                    ],
                )
                target_cursor.executemany(
                    '''INSERT INTO public."SysSolidSETInstanceResource"
                       ("IDSolidSETInstance", "IDResource", active)
                       VALUES (%s, %s, true)
                       ON CONFLICT ("IDSolidSETInstance", "IDResource")
                       DO UPDATE SET active=true''',
                    [(instance["ID"], resource_id) for resource_id in resource_ids],
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


def ingest_solidset_chat_resources(instance: dict[str, object]) -> dict[str, int]:
    """Sincroniza las relaciones recurso-sala desde SolidSET."""
    source_rows = read_dataset(instance.get("DataAPI") or {}, "workroom-resources")

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


def ingest_solidset_workrooms(instance: dict[str, object]) -> dict[str, int]:
    """Sincroniza el catálogo de canales de SolidSET hacia PostgreSQL."""
    source_rows = read_dataset(instance.get("DataAPI") or {}, "workrooms")

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
