from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

import pymssql

from app.config import settings
from app.llm.secrets import decrypt_api_key, encrypt_api_key

_current_instance: ContextVar[dict[str, Any] | None] = ContextVar(
    "solidset_sql_instance", default=None,
)


def encrypt_sql_password(value: str | None) -> str | None:
    """Uses the deployment master key; SQL credentials never remain in clear text."""
    return encrypt_api_key(value)


def decrypt_sql_password(value: str | None) -> str:
    return decrypt_api_key(value)


def connection_options(instance: dict[str, Any]) -> dict[str, Any]:
    database = instance.get("Database") or {}
    host = str(database.get("Host") or "").strip().rstrip("\\")
    if not host:
        raise RuntimeError("A instância SolidSET não tem uma ligação SQL Server configurada.")
    instance_name = str(database.get("InstanceName") or "").strip().strip("\\")
    server = f"{host}\\{instance_name}" if instance_name else host
    options: dict[str, Any] = {"server": server}
    port = int(database.get("Port") or 0)
    if not instance_name and port:
        options["port"] = port
    return options


@contextmanager
def connect(instance: dict[str, Any], *, as_dict: bool = False) -> Iterator[pymssql.Connection]:
    database = instance.get("Database") or {}
    if not database.get("active", True):
        raise RuntimeError("A ligação SQL Server desta instância está desativada.")
    connection = pymssql.connect(
        **connection_options(instance),
        user=str(database.get("Username") or ""),
        password=decrypt_sql_password(database.get("EncryptedPassword")),
        database=str(database.get("DatabaseName") or ""),
        login_timeout=max(3, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
        timeout=max(10, settings.DB_INGEST_QUERY_TIMEOUT_SECONDS),
        as_dict=as_dict,
    )
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def instance_context(instance: dict[str, Any]) -> Iterator[None]:
    token = _current_instance.set(instance)
    try:
        yield
    finally:
        _current_instance.reset(token)


def open_current_connection(*, as_dict: bool = False) -> pymssql.Connection:
    instance = _current_instance.get()
    if not instance:
        raise RuntimeError("Não existe uma instância SolidSET no contexto SQL atual.")
    database = instance.get("Database") or {}
    return pymssql.connect(
        **connection_options(instance), user=str(database.get("Username") or ""),
        password=decrypt_sql_password(database.get("EncryptedPassword")),
        database=str(database.get("DatabaseName") or ""),
        login_timeout=max(3, settings.DB_INGEST_CONNECT_TIMEOUT_SECONDS),
        timeout=max(10, settings.DB_INGEST_QUERY_TIMEOUT_SECONDS), as_dict=as_dict,
    )


def test_connection(instance: dict[str, Any]) -> dict[str, Any]:
    with connect(instance, as_dict=True) as connection:
        with connection.cursor(as_dict=True) as cursor:
            cursor.execute("SELECT DB_NAME() AS DatabaseName, @@VERSION AS ServerVersion")
            row = cursor.fetchone() or {}
            cursor.execute(
                "SELECT CASE WHEN OBJECT_ID('dbo.SysResource2Agent') IS NULL "
                "THEN 0 ELSE 1 END AS HasResourceAgent"
            )
            capabilities = cursor.fetchone() or {}
    return {
        "connected": True,
        "databaseName": row.get("DatabaseName"),
        "serverVersion": str(row.get("ServerVersion") or "")[:255],
        "adapterCode": (instance.get("Database") or {}).get("AdapterCode") or "solidset-v1",
        "hasSysResource2Agent": bool(capabilities.get("HasResourceAgent")),
    }
