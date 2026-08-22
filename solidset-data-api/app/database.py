from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
from time import perf_counter
from typing import Any, Iterator
from uuid import UUID

import pymssql

from app.config import settings


def _connection_target() -> str:
    instance = (settings.SQL_SERVER_INSTANCE or "").strip()
    port = settings.SQL_SERVER_PORT if not instance else "-"
    return (
        f"host={settings.SQL_SERVER_HOST.strip()} "
        f"instance={instance or '-'} port={port} "
        f"database={settings.SQL_SERVER_DATABASE}"
    )


def _safe_error(exc: BaseException) -> str:
    """Return a bounded diagnostic without credentials or multiline payloads."""
    message = " ".join(str(exc).split())
    for secret in (settings.SQL_SERVER_PASSWORD, settings.SQL_SERVER_USERNAME):
        if secret:
            message = message.replace(secret, "***")
    return message[:600]


@contextmanager
def connection(*, as_dict: bool = True) -> Iterator[pymssql.Connection]:
    options: dict[str, Any] = {"server": settings.server()}
    if not settings.SQL_SERVER_INSTANCE and settings.SQL_SERVER_PORT:
        options["port"] = settings.SQL_SERVER_PORT
    started_at = perf_counter()
    target = _connection_target()
    print(f"🔌 SQL_CONNECT start {target}", flush=True)
    try:
        conn = pymssql.connect(
            **options,
            user=settings.SQL_SERVER_USERNAME,
            password=settings.SQL_SERVER_PASSWORD,
            database=settings.SQL_SERVER_DATABASE,
            login_timeout=max(3, settings.SQL_SERVER_LOGIN_TIMEOUT),
            timeout=max(10, settings.SQL_SERVER_QUERY_TIMEOUT),
            as_dict=as_dict,
        )
    except Exception as exc:
        elapsed_ms = (perf_counter() - started_at) * 1000
        print(
            f"❌ SQL_CONNECT failed {target} duration_ms={elapsed_ms:.1f} "
            f"error_type={type(exc).__name__} error={_safe_error(exc)}",
            flush=True,
        )
        raise
    elapsed_ms = (perf_counter() - started_at) * 1000
    print(f"✅ SQL_CONNECT connected {target} duration_ms={elapsed_ms:.1f}", flush=True)
    try:
        yield conn
    finally:
        conn.close()


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def execute_read(
    query: str,
    parameters: list[Any],
    max_rows: int,
    *,
    operation: str = "read-query",
) -> tuple[list[str], list[Any]]:
    started_at = perf_counter()
    query_id = hashlib.sha256(query.encode("utf-8", errors="replace")).hexdigest()[:12]
    print(
        f"🗄️ SQL_READ start operation={operation} query_id={query_id} "
        f"parameter_count={len(parameters)} max_rows={max_rows}",
        flush=True,
    )
    try:
        with connection(as_dict=True) as conn, conn.cursor(as_dict=True) as cursor:
            cursor.execute(query, tuple(parameters))
            rows = cursor.fetchmany(max_rows + 1)
            if len(rows) > max_rows:
                raise ValueError(f"A consulta excedeu o limite de {max_rows} linhas.")
            columns = [str(item[0]) for item in (cursor.description or [])]
            normalized = [
                {str(key): json_value(value) for key, value in dict(row).items()}
                for row in rows
            ]
    except Exception as exc:
        elapsed_ms = (perf_counter() - started_at) * 1000
        print(
            f"❌ SQL_READ failed operation={operation} query_id={query_id} "
            f"duration_ms={elapsed_ms:.1f} error_type={type(exc).__name__} "
            f"error={_safe_error(exc)}",
            flush=True,
        )
        raise
    elapsed_ms = (perf_counter() - started_at) * 1000
    print(
        f"✅ SQL_READ completed operation={operation} query_id={query_id} "
        f"rows={len(normalized)} columns={len(columns)} duration_ms={elapsed_ms:.1f}",
        flush=True,
    )
    return columns, normalized
