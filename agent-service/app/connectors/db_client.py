from __future__ import annotations

from typing import Any
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
    """Inserta una configuración y deja que PostgreSQL genere su UUID."""
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
                RETURNING *
                ''',
                values,
            )
            saved = cursor.fetchone()

    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió la configuración guardada.")
    return dict(saved)
