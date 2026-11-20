from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import re
from time import perf_counter
from typing import Any, Iterator
from uuid import UUID

import pymssql

from app.config import settings


def _connection_target() -> str:
    instance = (settings.SQL_SERVER_INSTANCE or "").strip()
    port = settings.SQL_SERVER_PORT if not instance else "-"
    return (
        f"host={settings.host()} "
        f"instance={instance or '-'} port={port} "
        f"database={settings.SQL_SERVER_DATABASE}"
    )


def _safe_error(exc: BaseException) -> str:
    """Return a bounded diagnostic without credentials or multiline payloads."""
    message = " ".join(str(exc).split())
    if settings.SQL_SERVER_PASSWORD:
        message = message.replace(settings.SQL_SERVER_PASSWORD, "***")
    if settings.SQL_SERVER_USERNAME:
        username = re.escape(settings.SQL_SERVER_USERNAME)
        message = re.sub(
            rf"(?i)(user(?:name)?\s*[=:]?\s*['\"]?){username}(['\"]?)",
            r"\1***\2",
            message,
        )
    return message[:600]


@contextmanager
def connection(*, as_dict: bool = True) -> Iterator[pymssql.Connection]:
    options: dict[str, Any] = {"server": settings.server()}
    if not settings.SQL_SERVER_INSTANCE and settings.SQL_SERVER_PORT:
        options["port"] = settings.SQL_SERVER_PORT
    started_at = perf_counter()
    target = _connection_target()
    pass
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
        pass
        raise
    elapsed_ms = (perf_counter() - started_at) * 1000
    pass
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
    pass
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
        pass
        raise
    elapsed_ms = (perf_counter() - started_at) * 1000
    pass
    return columns, normalized
