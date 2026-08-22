from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
from typing import Any, Iterator

from app.config import settings
from app.llm.secrets import decrypt_api_key, encrypt_api_key
from app.connectors.solidset_data_api import connect as connect_data_api

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
    if (
        host.lower() in {"localhost", "127.0.0.1", "."}
        and (os.path.exists("/.dockerenv") or os.getenv("RUNNING_IN_DOCKER") == "1")
    ):
        host = "host.docker.internal"
    instance_name = str(database.get("InstanceName") or "").strip().strip("\\")
    server = f"{host}\\{instance_name}" if instance_name else host
    options: dict[str, Any] = {"server": server}
    port = int(database.get("Port") or 0)
    if not instance_name and port:
        options["port"] = port
    return options


@contextmanager
def connect(instance: dict[str, Any], *, as_dict: bool = False) -> Iterator[Any]:
    data_api = instance.get("DataAPI") or {}
    if data_api.get("active") and str(data_api.get("BaseUrl") or "").strip():
        connection = connect_data_api(data_api, as_dict=as_dict)
        try:
            yield connection
        finally:
            connection.close()
        return
    raise RuntimeError(
        "A instância SolidSET não tem uma SolidSET Data API ativa; "
        "o acesso SQL Server direto está desativado."
    )


@contextmanager
def instance_context(instance: dict[str, Any]) -> Iterator[None]:
    token = _current_instance.set(instance)
    try:
        yield
    finally:
        _current_instance.reset(token)


def current_instance() -> dict[str, Any] | None:
    """Return the request-scoped instance without exposing mutable global state."""
    instance = _current_instance.get()
    return dict(instance) if instance else None


def open_current_connection(*, as_dict: bool = False) -> Any:
    instance = _current_instance.get()
    if not instance:
        raise RuntimeError("Não existe uma instância SolidSET no contexto SQL atual.")
    data_api = instance.get("DataAPI") or {}
    if data_api.get("active") and str(data_api.get("BaseUrl") or "").strip():
        return connect_data_api(data_api, as_dict=as_dict)
    raise RuntimeError(
        "A instância SolidSET não tem uma SolidSET Data API ativa; "
        "o acesso SQL Server direto está desativado."
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
