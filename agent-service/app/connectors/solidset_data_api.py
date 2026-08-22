from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx

from app.config import settings
from app.llm.secrets import decrypt_api_key


class SolidSETDataAPIError(RuntimeError):
    pass


def _runtime_base_url(value: str) -> str:
    """Resolve host-local URLs from inside the agent container."""
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    running_in_docker = (
        os.path.exists("/.dockerenv")
        or os.getenv("RUNNING_IN_DOCKER") == "1"
    )
    if running_in_docker and (parsed.hostname or "").lower() in {
        "localhost", "127.0.0.1", "::1",
    }:
        port = f":{parsed.port}" if parsed.port else ""
        parsed = parsed._replace(netloc=f"host.docker.internal{port}")
        return urlunsplit(parsed).rstrip("/")
    return base_url


def _json_parameter(value: Any) -> Any:
    if isinstance(value, (datetime, date, time, UUID, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _strip_sql_comments(query: str) -> str:
    """Remove legacy comments before sending a single read statement."""
    without_blocks = re.sub(r"/\*.*?\*/", " ", str(query), flags=re.DOTALL)
    without_lines = re.sub(r"--[^\r\n]*", " ", without_blocks)
    return "\n".join(
        line.rstrip() for line in without_lines.splitlines() if line.strip()
    ).strip()


class DataAPICursor:
    def __init__(self, connection: "DataAPIConnection", *, as_dict: bool) -> None:
        self.connection = connection
        self.as_dict = as_dict
        self.description: list[tuple[Any, ...]] = []
        self._rows: list[Any] = []
        self._offset = 0

    def __enter__(self) -> "DataAPICursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._rows = []
        self._offset = 0

    def execute(self, query: str, params: Any = None) -> None:
        parameters = list(params or [])
        payload = {
            "query": _strip_sql_comments(str(query)),
            "parameters": [_json_parameter(value) for value in parameters],
            "maxRows": self.connection.max_rows,
        }
        try:
            response = self.connection.client.post("/api/v1/query/read", json=payload)
        except httpx.HTTPError as exc:
            raise SolidSETDataAPIError(f"SolidSET Data API indisponível: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise SolidSETDataAPIError(
                f"SolidSET Data API rejeitou a consulta (HTTP {response.status_code}): {detail}"
            )
        data = response.json()
        columns = [str(value) for value in data.get("columns") or []]
        self.description = [(column, None, None, None, None, None, None) for column in columns]
        dict_rows = [dict(row) for row in data.get("rows") or []]
        self._rows = dict_rows if self.as_dict else [tuple(row.get(c) for c in columns) for row in dict_rows]
        self._offset = 0

    def fetchone(self) -> Any:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchmany(self, size: int | None = None) -> list[Any]:
        count = max(0, int(size or 1))
        rows = self._rows[self._offset:self._offset + count]
        self._offset += len(rows)
        return rows

    def fetchall(self) -> list[Any]:
        rows = self._rows[self._offset:]
        self._offset = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class DataAPIConnection:
    def __init__(self, configuration: dict[str, Any], *, as_dict: bool = False) -> None:
        base_url = _runtime_base_url(configuration.get("BaseUrl") or "")
        if not base_url:
            raise SolidSETDataAPIError("A URL da SolidSET Data API não está configurada.")
        api_key = decrypt_api_key(configuration.get("EncryptedAPIKey"))
        if not api_key:
            raise SolidSETDataAPIError("A credencial da SolidSET Data API não está configurada.")
        self.default_as_dict = as_dict
        self.max_rows = int(configuration.get("MaxRows") or 5000)
        timeout = max(5, int(configuration.get("TimeoutSeconds") or 120))
        self.client = httpx.Client(
            base_url=base_url,
            headers={"X-SolidSET-Data-Key": api_key},
            timeout=httpx.Timeout(timeout),
            verify=bool(configuration.get("VerifyTLS", True)),
        )

    def __enter__(self) -> "DataAPIConnection":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def cursor(self, as_dict: bool | None = None) -> DataAPICursor:
        return DataAPICursor(
            self,
            as_dict=self.default_as_dict if as_dict is None else bool(as_dict),
        )

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.client.close()


def connect(configuration: dict[str, Any], *, as_dict: bool = False) -> DataAPIConnection:
    return DataAPIConnection(configuration, as_dict=as_dict)


def read_dataset(configuration: dict[str, Any], dataset: str) -> list[dict[str, Any]]:
    with DataAPIConnection(configuration, as_dict=True) as connection:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            try:
                response = connection.client.get(
                    f"/api/v1/datasets/{dataset}",
                    params={"offset": offset, "limit": connection.max_rows},
                )
            except httpx.HTTPError as exc:
                raise SolidSETDataAPIError(f"SolidSET Data API indisponível: {exc}") from exc
            if response.status_code >= 400:
                raise SolidSETDataAPIError(
                    f"Falha ao obter dataset {dataset} (HTTP {response.status_code})."
                )
            payload = response.json()
            page = [dict(row) for row in payload.get("rows") or []]
            rows.extend(page)
            if not payload.get("hasMore"):
                return rows
            next_offset = payload.get("nextOffset")
            if next_offset is None or int(next_offset) <= offset:
                raise SolidSETDataAPIError(
                    f"Paginação inválida no dataset {dataset}."
                )
            offset = int(next_offset)


def read_active_resource_agent(
    configuration: dict[str, Any], human_resource_id: str
) -> dict[str, Any] | None:
    with DataAPIConnection(configuration, as_dict=True) as connection:
        try:
            response = connection.client.get(f"/api/v1/agents/{human_resource_id}")
        except httpx.HTTPError as exc:
            raise SolidSETDataAPIError(f"SolidSET Data API indisponível: {exc}") from exc
        if response.status_code >= 400:
            raise SolidSETDataAPIError(
                f"Falha ao validar agente (HTTP {response.status_code})."
            )
        rows = response.json().get("rows") or []
        return dict(rows[0]) if rows else None
