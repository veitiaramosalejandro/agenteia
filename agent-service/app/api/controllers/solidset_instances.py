from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas.common import (
    SolidSETDataAPIConnectionTestResponse,
    SolidSETInstanceConfiguration,
    SolidSETInstanceConfigurationResponse,
    SolidSETInstanceListResponse,
    SolidSETInstanceStored,
    SysResourceIAConfiguration,
    SysResourceIAConfigurationResponse,
    SysResourceIAConfigurationStored,
)
from app.connectors.db_client import (
    get_solidset_instance,
    get_solidset_schema_snapshot,
    list_active_solidset_instances,
    save_solidset_instance,
    save_solidset_schema_snapshot,
    save_sys_resource_ia,
)
from app.connectors.solidset_data_api import read_schema_catalog
from app.connectors.solidset_sql import test_connection as test_solidset_sql_connection
from app.services.instance_resolution import clear_instance_cache


router = APIRouter(tags=["SolidSET Configuration"])


@router.post(
    "/api/v1/agent/solidset/chat-configuration",
    response_model=SysResourceIAConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an AI resource configuration",
)
def save_solidset_chat_configuration(
    configuration: SysResourceIAConfiguration,
) -> SysResourceIAConfigurationResponse:
    """Guarda en PostgreSQL la configuración de chat IA recibida de SolidSET."""
    payload = (
        configuration.model_dump()
        if hasattr(configuration, "model_dump")
        else configuration.dict()
    )
    try:
        saved = save_sys_resource_ia(payload)
    except psycopg.Error as exc:
        print(f"❌ No se pudo guardar la configuración SysResourceIA: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível guardar a configuração no PostgreSQL.",
        ) from exc

    return SysResourceIAConfigurationResponse(
        status="saved",
        configuration=SysResourceIAConfigurationStored(**saved),
    )


@router.post(
    "/api/v1/agent/solidset/instances",
    response_model=SolidSETInstanceConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register or update a SolidSET instance and its Data API",
)
def register_solidset_instance(
    configuration: SolidSETInstanceConfiguration,
) -> SolidSETInstanceConfigurationResponse:
    """Registers instance routing, regional settings, and its independent Data API."""
    payload = configuration.model_dump()
    for field in ("BaseUrl", "NotificationUrl"):
        value = str(payload.get(field) or "").strip().rstrip("/")
        if field == "BaseUrl" or value:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{field} deve ser um URL HTTP(S) absoluto.",
                )
        payload[field] = value or None
    payload["Code"] = payload["Code"].strip()
    payload["Name"] = payload["Name"].strip()
    payload["SourceIP"] = str(payload.get("SourceIP") or "").strip() or None
    payload["CountryCode"] = payload["CountryCode"].strip().upper()
    language, *locale_parts = payload["Locale"].strip().split("-")
    payload["Locale"] = "-".join(
        [language.lower(), *[part.upper() for part in locale_parts]]
    )
    payload["TimeZone"] = payload["TimeZone"].strip()
    data_api_payload = payload.get("DataAPI")
    if data_api_payload:
        data_api_url = str(data_api_payload.get("BaseUrl") or "").strip().rstrip("/")
        parsed_data_api = urlparse(data_api_url)
        if (
            parsed_data_api.scheme not in {"http", "https"}
            or not parsed_data_api.netloc
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="DataAPI.BaseUrl deve ser um URL HTTP(S) absoluto.",
            )
        data_api_payload["BaseUrl"] = data_api_url
    try:
        ZoneInfo(payload["TimeZone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="TimeZone deve ser um identificador IANA válido, por exemplo Europe/Lisbon.",
        ) from exc
    try:
        saved = save_solidset_instance(payload)
        operation = str(saved.get("_operation", "saved"))
        clear_instance_cache()
        saved = get_solidset_instance(code=payload["Code"], source_ip=None) or saved
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="SourceIP já está atribuído a outra instância SolidSET.",
        ) from exc
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível guardar a instância SolidSET no PostgreSQL.",
        ) from exc
    saved.pop("_operation", None)
    data_api = saved.get("DataAPI")
    if data_api:
        saved["DataAPI"] = {
            key: data_api.get(key)
            for key in (
                "BaseUrl",
                "TimeoutSeconds",
                "MaxRows",
                "VerifyTLS",
                "active",
            )
        }
        saved["DataAPI"]["APIKeyConfigured"] = bool(data_api.get("EncryptedAPIKey"))
    return SolidSETInstanceConfigurationResponse(
        status=operation,
        configuration=SolidSETInstanceStored(**saved),
    )


def _public_solidset_instance(instance: dict[str, Any]) -> dict[str, Any]:
    """Returns instance configuration without encrypted or clear-text credentials."""
    public = dict(instance)
    data_api = public.get("DataAPI")
    if isinstance(data_api, dict):
        public["DataAPI"] = {
            key: data_api.get(key)
            for key in (
                "BaseUrl",
                "TimeoutSeconds",
                "MaxRows",
                "VerifyTLS",
                "active",
            )
        }
        public["DataAPI"]["APIKeyConfigured"] = bool(
            data_api.get("EncryptedAPIKey") or data_api.get("APIKeyConfigured")
        )
    else:
        public["DataAPI"] = None
    public.pop("EncryptedAPIKey", None)
    public.pop("APIKey", None)
    return public


@router.get(
    "/api/v1/agent/solidset/instances",
    response_model=SolidSETInstanceListResponse,
    summary="List SolidSET instances",
)
def read_solidset_instances(
    activeOnly: bool = Query(False, description="Return only active instances."),
) -> SolidSETInstanceListResponse:
    """Lists configured instances without exposing Data API credentials."""
    try:
        rows = list_active_solidset_instances(active_only=activeOnly)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível consultar as instâncias SolidSET."
        ) from exc
    items = [SolidSETInstanceStored(**_public_solidset_instance(row)) for row in rows]
    return SolidSETInstanceListResponse(total=len(items), items=items)


@router.get(
    "/api/v1/agent/solidset/instances/{code}",
    response_model=SolidSETInstanceStored,
    summary="Get one SolidSET instance",
)
def read_solidset_instance(code: str) -> SolidSETInstanceStored:
    """Returns one configured instance by code, including inactive instances."""
    normalized_code = code.strip()
    if not normalized_code:
        raise HTTPException(status_code=422, detail="Code é obrigatório.")
    try:
        instance = get_solidset_instance(
            code=normalized_code,
            source_ip=None,
            active_only=False,
        )
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível consultar a instância SolidSET."
        ) from exc
    if not instance:
        raise HTTPException(status_code=404, detail="A instância SolidSET não existe.")
    return SolidSETInstanceStored(**_public_solidset_instance(instance))


@router.post(
    "/api/v1/agent/solidset/instances/{code}/test-connection",
    response_model=SolidSETDataAPIConnectionTestResponse,
    summary="Test the configured SolidSET data provider",
)
def test_solidset_instance_database(code: str) -> SolidSETDataAPIConnectionTestResponse:
    """Tests connectivity and basic schema capabilities without exposing credentials."""
    instance = get_solidset_instance(code=code.strip(), source_ip=None)
    if not instance:
        raise HTTPException(
            status_code=404, detail="A instância SolidSET não existe ou está inativa."
        )
    data_api = instance.get("DataAPI") or {}
    if not data_api:
        raise HTTPException(
            status_code=409,
            detail="A instância não tem um fornecedor de dados configurado.",
        )
    print(
        "🔍 Testando SolidSET Data API "
        f"instance={instance.get('Code')} base={data_api.get('BaseUrl')}"
    )
    try:
        result = test_solidset_sql_connection(instance)
    except Exception as exc:
        print(
            "❌ Teste SolidSET Data API falhou "
            f"instance={instance.get('Code')} error={type(exc).__name__}: {str(exc)[:500]}"
        )
        raise HTTPException(
            status_code=503,
            detail="Não foi possível estabelecer ligação ao fornecedor de dados desta instância.",
        ) from exc
    return SolidSETDataAPIConnectionTestResponse(
        status="connected",
        instanceCode=str(instance["Code"]),
        **result,
    )


@router.post(
    "/api/v1/agent/solidset/instances/{code}/schema/refresh",
    summary="Refresh the SQL schema snapshot for a SolidSET instance",
)
def refresh_solidset_instance_schema(code: str) -> dict[str, Any]:
    """Reads the schema through Data API and stores the snapshot in PostgreSQL."""
    instance = get_solidset_instance(code=code.strip(), source_ip=None)
    if not instance:
        raise HTTPException(
            status_code=404, detail="A instância SolidSET não existe ou está inativa."
        )
    if not instance.get("DataAPI"):
        raise HTTPException(
            status_code=409,
            detail="A instância não tem uma SolidSET Data API configurada.",
        )
    try:
        catalog = read_schema_catalog(instance["DataAPI"])
        saved = save_solidset_schema_snapshot(instance["ID"], catalog)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível atualizar o catálogo de esquema da instância.",
        ) from exc
    return {
        "status": "updated",
        "instanceCode": str(instance["Code"]),
        "databaseName": catalog.get("databaseName"),
        "tableCount": len(catalog.get("tables") or []),
        "schemaHash": saved.get("SchemaHash"),
        "capturedAt": saved.get("CapturedAt"),
    }


@router.get(
    "/api/v1/agent/solidset/instances/{code}/schema",
    summary="Get the cached SQL schema snapshot for a SolidSET instance",
)
def get_solidset_instance_schema(code: str) -> dict[str, Any]:
    """Returns the PostgreSQL snapshot without opening a SQL Server connection."""
    instance = get_solidset_instance(code=code.strip(), source_ip=None)
    if not instance:
        raise HTTPException(
            status_code=404, detail="A instância SolidSET não existe ou está inativa."
        )
    snapshot = get_solidset_schema_snapshot(instance["ID"])
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail="Ainda não existe um snapshot de esquema para esta instância.",
        )
    return {
        "instanceCode": str(instance["Code"]),
        "databaseName": snapshot.get("DatabaseName"),
        "schemaHash": snapshot.get("SchemaHash"),
        "capturedAt": snapshot.get("CapturedAt"),
        "catalog": snapshot.get("Catalog"),
    }
