from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

import pymssql

from app.config import settings


@contextmanager
def connection(*, as_dict: bool = True) -> Iterator[pymssql.Connection]:
    options: dict[str, Any] = {"server": settings.server()}
    if not settings.SQL_SERVER_INSTANCE and settings.SQL_SERVER_PORT:
        options["port"] = settings.SQL_SERVER_PORT
    conn = pymssql.connect(
        **options,
        user=settings.SQL_SERVER_USERNAME,
        password=settings.SQL_SERVER_PASSWORD,
        database=settings.SQL_SERVER_DATABASE,
        login_timeout=max(3, settings.SQL_SERVER_LOGIN_TIMEOUT),
        timeout=max(10, settings.SQL_SERVER_QUERY_TIMEOUT),
        as_dict=as_dict,
    )
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


def execute_read(query: str, parameters: list[Any], max_rows: int) -> tuple[list[str], list[Any]]:
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
        return columns, normalized

