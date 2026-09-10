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
from app.connectors.agent_scope_query import AGENT_SCOPES_QUERY
from app.llm.secrets import decrypt_api_key


class SolidSETDataAPIError(RuntimeError):
    pass


def _reject_redirect(response: httpx.Response, operation: str) -> None:
    """Never forward the private Data API key through an unexpected redirect."""
    if not 300 <= response.status_code < 400:
        return
    raw_location = str(response.headers.get("location") or "")
    parsed = urlsplit(raw_location)
    safe_location = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    print(
        f"SOLIDSET_DATA_API_REDIRECT operation={operation} "
        f"status={response.status_code} destination={safe_location or 'unknown'}",
        flush=True,
    )
    raise SolidSETDataAPIError(
        "A SolidSET Data API devolveu uma redireção inesperada. "
        "Verifique a URL e a autenticação não interativa do gateway."
    )


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
        _reject_redirect(response, "query-read")
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


def _read_legacy_agent_scopes(connection: DataAPIConnection) -> list[dict[str, Any]]:
    """Read the fixed dataset through the authenticated gateway on older APIs.

    Never return a partial snapshot: the caller replaces active scope records.
    No model-generated SQL or direct SQL Server connection is involved.
    """
    with connection.cursor(as_dict=True) as cursor:
        count_query = f"SELECT COUNT_BIG(*) AS total FROM ({AGENT_SCOPES_QUERY}) AS scopes"
        cursor.execute(count_query)
        total = int(cursor.fetchone()["total"])
        if total < 0 or total > 1000000:
            raise SolidSETDataAPIError("Quantidade de alcances fora do limite de sincronização.")
        page_size = max(1, min(connection.max_rows, 1000))
        rows: list[dict[str, Any]] = []
        for offset in range(0, total, page_size):
            expected = min(page_size, total - offset)
            cursor.execute(
                AGENT_SCOPES_QUERY
                + " ORDER BY ResourceId, IDLogin, IDWorkRoom, IDCommunity,"
                " organizationid, organization_no, accountname"
                " OFFSET %s ROWS FETCH NEXT %s ROWS ONLY",
                (offset, expected),
            )
            page = cursor.fetchall()
            if len(page) != expected:
                raise SolidSETDataAPIError("Leitura incompleta dos alcances; sincronização cancelada.")
            rows.extend(page)
        cursor.execute(count_query)
        if int(cursor.fetchone()["total"]) != total:
            raise SolidSETDataAPIError("Os alcances mudaram durante a leitura; tente novamente.")
        return rows


def read_catalog_page(configuration: dict[str, Any], dataset: str, *, offset: int, limit: int) -> dict[str, Any]:
    """Read one bounded page of public catalog fields through the instance gateway."""
    fields = {
        "workrooms": ("IDWorkRoom", "Code", "Name", "Description"),
        "resources": ("ResourceId", "DisplayName", "ActiveIDLogin2Resource", "IDAgentResource", "FullName"),
    }
    if dataset not in fields or offset < 0 or not 1 <= limit <= 1000:
        raise ValueError("Invalid catalog or pagination")
    try:
        with DataAPIConnection(configuration, as_dict=True) as connection:
            page_size = min(limit, max(1, connection.max_rows))
            response = connection.client.get(
                f"/api/v1/datasets/{dataset}", params={"offset": offset, "limit": page_size}
            )
            _reject_redirect(response, f"catalog-{dataset}")
            response.raise_for_status()
            payload = response.json()
        rows = payload["rows"]
        effective_limit = payload.get("limit", page_size)
        if (not isinstance(rows, list) or not isinstance(effective_limit, int)
                or not 1 <= effective_limit <= page_size or len(rows) > effective_limit
                or not all(isinstance(row, dict) for row in rows)):
            raise ValueError("Invalid catalog page")
        has_more = payload.get("hasMore", False)
        if not isinstance(has_more, bool) or (has_more and not rows):
            raise ValueError("Invalid catalog pagination")
        return {
            "rows": [{field: row.get(field) for field in fields[dataset]} for row in rows],
            "rowCount": len(rows), "offset": offset, "limit": effective_limit,
            "hasMore": has_more, "nextOffset": offset + len(rows) if has_more else None,
        }
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise SolidSETDataAPIError("Não foi possível ler o catálogo SolidSET.") from exc


def read_dataset(configuration: dict[str, Any], dataset: str) -> list[dict[str, Any]]:
    with DataAPIConnection(configuration, as_dict=True) as connection:
        rows: list[dict[str, Any]] = []
        offset = 0
        pages = 0
        # Large description fields make 5,000-row responses unreliable through
        # HTTPS tunnels. Keep each transfer bounded while retaining full pagination.
        page_size = min(connection.max_rows, 1000)
        while True:
            try:
                response = connection.client.get(
                    f"/api/v1/datasets/{dataset}",
                    params={"offset": offset, "limit": page_size},
                )
            except httpx.HTTPError as exc:
                print(
                    f"SOLIDSET_DATASET_REQUEST_FAILED dataset={dataset} "
                    f"offset={offset} limit={page_size} type={type(exc).__name__}",
                    flush=True,
                )
                raise SolidSETDataAPIError(f"SolidSET Data API indisponível: {exc}") from exc
            _reject_redirect(response, f"dataset-{dataset}")
            if response.status_code == 404 and dataset == "agent-scopes" and offset == 0:
                try:
                    missing_dataset = response.json().get("detail") == "O conjunto de dados não existe."
                except (ValueError, AttributeError):
                    missing_dataset = False
                if missing_dataset:
                    return _read_legacy_agent_scopes(connection)
            if response.status_code >= 400:
                raise SolidSETDataAPIError(
                    f"Falha ao obter dataset {dataset} (HTTP {response.status_code})."
                )
            try:
                payload = response.json()
            except (ValueError, TypeError) as exc:
                content_type = str(response.headers.get("content-type") or "unknown")[:120]
                print(
                    f"SOLIDSET_DATASET_INVALID_RESPONSE dataset={dataset} "
                    f"status={response.status_code} content_type={content_type} "
                    f"body_bytes={len(response.content)}",
                    flush=True,
                )
                raise SolidSETDataAPIError(
                    f"Resposta inválida da SolidSET Data API para o dataset {dataset}."
                ) from exc
            if not isinstance(payload, dict):
                raise SolidSETDataAPIError(
                    f"Resposta inválida do dataset {dataset}: objeto JSON esperado."
                )
            raw_page = payload.get("rows")
            if not isinstance(raw_page, list) or not all(
                isinstance(row, dict) for row in raw_page
            ):
                raise SolidSETDataAPIError(
                    f"Resposta inválida do dataset {dataset}: rows deve ser uma lista de objetos."
                )
            has_more = payload.get("hasMore", False)
            if not isinstance(has_more, bool):
                raise SolidSETDataAPIError(
                    f"Resposta inválida do dataset {dataset}: hasMore deve ser booleano."
                )
            page = [dict(row) for row in raw_page]
            rows.extend(page)
            pages += 1
            if not has_more:
                print(
                    f"SOLIDSET_DATASET_READ dataset={dataset} rows={len(rows)} "
                    f"pages={pages} page_size={page_size}",
                    flush=True,
                )
                return rows
            next_offset = payload.get("nextOffset")
            try:
                parsed_next_offset = int(next_offset)
            except (TypeError, ValueError) as exc:
                raise SolidSETDataAPIError(
                    f"Paginação inválida no dataset {dataset}."
                ) from exc
            if parsed_next_offset <= offset or not page:
                raise SolidSETDataAPIError(
                    f"Paginação inválida no dataset {dataset}."
                )
            offset = parsed_next_offset


def read_active_resource_agent(
    configuration: dict[str, Any], human_resource_id: str
) -> dict[str, Any] | None:
    with DataAPIConnection(configuration, as_dict=True) as connection:
        try:
            response = connection.client.get(f"/api/v1/agents/{human_resource_id}")
        except httpx.HTTPError as exc:
            raise SolidSETDataAPIError(f"SolidSET Data API indisponível: {exc}") from exc
        _reject_redirect(response, "active-resource-agent")
        if response.status_code >= 400:
            raise SolidSETDataAPIError(
                f"Falha ao validar agente (HTTP {response.status_code})."
            )
        rows = response.json().get("rows") or []
        return dict(rows[0]) if rows else None


def read_resource_identity(
    configuration: dict[str, Any], resource_id: str
) -> dict[str, Any] | None:
    """Verifica un recurso autónomo y su vínculo de login directamente en SolidSET."""
    with DataAPIConnection(configuration, as_dict=True) as connection:
        with connection.cursor(as_dict=True) as cursor:
            cursor.execute(
                """
                SELECT TOP 1 r.ResourceId, r.DisplayName,
                       r.ActiveIDLogin2Resource, l.IDLogin, l.FullName
                FROM dbo.SysResources r WITH (NOLOCK)
                INNER JOIN dbo.SysLogin l WITH (NOLOCK)
                  ON l.ActiveIDLogin2Resource = r.ActiveIDLogin2Resource
                WHERE r.ResourceId = %s
                ORDER BY l.IDLogin
                """,
                (resource_id,),
            )
            row = cursor.fetchone()
    return dict(row) if row else None


def read_schema_catalog(
    configuration: dict[str, Any], tables: list[str] | None = None
) -> dict[str, Any]:
    """Read a structured schema fragment from the instance Data API."""
    with DataAPIConnection(configuration, as_dict=True) as connection:
        params = {"tables": ",".join(tables or [])} if tables else None
        try:
            response = connection.client.get("/api/v1/schema/catalog", params=params)
        except httpx.HTTPError as exc:
            raise SolidSETDataAPIError(f"SolidSET Data API indisponível: {exc}") from exc
        _reject_redirect(response, "schema-catalog")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise SolidSETDataAPIError(
                f"Falha ao obter o catálogo de esquema (HTTP {response.status_code}): {detail}"
            )
        payload = response.json()
        return {
            "databaseName": str(payload.get("databaseName") or ""),
            "tables": [dict(value) for value in payload.get("tables") or []],
        }
