from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request

from app.api.schemas.llm_configuration import (
    AgentIAModelConfiguration,
    AgentIAModelStored,
    LLMProviderConfiguration,
    LLMProviderConfigurationResponse,
    LLMProviderConfigurationStored,
    LLMProviderFromEnvironment,
)
from app.config import settings
from app.connectors.db_client import (
    deactivate_llm_provider_configuration,
    get_agent_model_configurations,
    list_llm_provider_configurations,
    save_agent_model_configuration,
    save_llm_provider_configuration,
)
from app.llm import LLMProviderConfig, ProviderRegistry, create_chat_model
from app.llm.providers import provider_config_from_settings, NVIDIA_BASE_URL


router = APIRouter(tags=["LLM Providers"])


def _clear_cache(request: Request) -> None:
    runtime_agent = getattr(request.app.state, "agent", None)
    if runtime_agent is not None:
        runtime_agent.clear_llm_configuration_cache()


@router.put(
    "/api/v1/agent/llm/providers/{code}",
    response_model=LLMProviderConfigurationResponse,
)
def save_llm_provider(
    request: Request,
    code: str,
    configuration: LLMProviderConfiguration,
) -> LLMProviderConfigurationResponse:
    return _save_llm_provider(request, code, configuration)


def _save_llm_provider(request: Request, code: str, configuration: LLMProviderConfiguration,
                       *, create_only: bool = False) -> LLMProviderConfigurationResponse:
    payload = configuration.model_dump()
    if code.strip().lower() != payload["Code"].strip().lower():
        raise HTTPException(
            status_code=422, detail="O Code da rota e do corpo devem coincidir."
        )
    provider = payload["Provider"].strip().lower().replace("-", "_")
    if provider not in ProviderRegistry.names():
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Fornecedor LLM não suportado.",
                "available": list(ProviderRegistry.names()),
            },
        )
    payload.update(
        Code=payload["Code"].strip(),
        Name=payload["Name"].strip(),
        Provider=provider,
        Model=payload["Model"].strip(),
    )
    for field in ("BaseUrl", "AzureEndpoint"):
        value = str(payload.get(field) or "").strip().rstrip("/")
        if value:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(
                    status_code=422, detail=f"{field} deve ser um URL HTTP(S) absoluto."
                )
        payload[field] = value or None
    if provider == "nvidia":
        payload["BaseUrl"] = payload.get("BaseUrl") or NVIDIA_BASE_URL
        payload["UseResponsesAPI"] = False
        payload["StoreResponses"] = False
    if provider == "ollama" and not payload.get("BaseUrl"):
        payload["BaseUrl"] = settings.OLLAMA_BASE_URL.rstrip("/")
    if provider in {"openai_compatible", "local_openai"} and not payload.get("BaseUrl"):
        raise HTTPException(
            status_code=422,
            detail="BaseUrl é obrigatório para um fornecedor compatível com OpenAI.",
        )
    if provider == "azure_openai" and not (
        payload.get("AzureEndpoint") or payload.get("BaseUrl")
    ):
        raise HTTPException(
            status_code=422,
            detail="AzureEndpoint ou BaseUrl é obrigatório para Azure OpenAI.",
        )
    try:
        create_chat_model(
            LLMProviderConfig(
                provider=provider,
                model=payload["Model"],
                base_url=payload.get("BaseUrl") or "",
                api_key=payload.get("APIKey") or "",
                temperature=payload["Temperature"],
                max_output_tokens=payload["MaxOutputTokens"],
                timeout_seconds=payload["TimeoutSeconds"],
                azure_endpoint=payload.get("AzureEndpoint") or "",
                azure_api_version=payload.get("AzureApiVersion") or "",
                azure_deployment=payload.get("AzureDeployment") or "",
                organization=payload.get("OpenAIOrganization") or "",
                project=payload.get("OpenAIProject") or "",
                use_responses_api=payload.get("UseResponsesAPI", True),
                store_responses=payload.get("StoreResponses", False),
                max_retries=payload.get("MaxRetries", 2),
                service_tier=payload.get("ServiceTier", "auto"),
            )
        )
        saved = save_llm_provider_configuration(payload, create_only=True) if create_only else save_llm_provider_configuration(payload)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Ya existe una conexión con ese Code. Usa PUT para actualizarla.") from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(
            status_code=404, detail="O IDResource indicado não existe."
        ) from exc
    except (ValueError, RuntimeError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail="A configuração do fornecedor não é válida."
        ) from exc
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível guardar o fornecedor no PostgreSQL.",
        ) from exc
    _clear_cache(request)
    return LLMProviderConfigurationResponse(
        status="created" if create_only else "saved", configuration=LLMProviderConfigurationStored(**saved)
    )


@router.post(
    "/api/v1/agent/llm/providers/from-env",
    response_model=LLMProviderConfigurationResponse,
    status_code=201,
    summary="Crear una conexión LLM con los valores del .env",
    description="Crea una conexión general con los valores cargados del entorno. No acepta IDResource: asígnala después a un recurso mediante PUT /api/v1/agent/solidset/agents/{IDResource}/model. Las claves se cifran y no se devuelven. Un código existente devuelve 409. Crear no comprueba acceso al proveedor. Recrea el contenedor para aplicar cambios del .env.",
)
def create_llm_provider_from_environment(request: Request, configuration: LLMProviderFromEnvironment):
    cfg = provider_config_from_settings(settings)
    if configuration.Source == "nvidia":
        cfg = LLMProviderConfig(
            provider="nvidia", model=configuration.Model, base_url=NVIDIA_BASE_URL,
            api_key=settings.NVIDIA_API_KEY, temperature=1,
            use_responses_api=False, max_retries=0,
        )
    if configuration.Source == "openai_search":
        cfg = LLMProviderConfig(
            provider="openai", model=settings.OPENAI_SEARCH_MODEL,
            api_key=settings.OPENAI_API_KEY, base_url="https://api.openai.com/v1",
            max_output_tokens=settings.OPENAI_SEARCH_MAX_OUTPUT_TOKENS,
            timeout_seconds=settings.WEB_SEARCH_TIMEOUT_SECONDS,
            organization=settings.OPENAI_ORGANIZATION, project=settings.OPENAI_PROJECT,
            max_retries=settings.OPENAI_MAX_RETRIES, service_tier=settings.OPENAI_SERVICE_TIER,
            use_responses_api=True, store_responses=False,
        )
    # Never copy the OpenAI environment credential into a different provider.
    if configuration.Source == "runtime" and cfg.provider not in {"openai", "azure_openai", "nvidia"}:
        from dataclasses import replace
        cfg = replace(cfg, api_key=settings.LLM_API_KEY)
    if cfg.provider in {"openai", "azure_openai", "anthropic", "gemini", "nvidia"} and not cfg.api_key:
        raise HTTPException(status_code=422, detail="Falta la clave API del proveedor en el entorno del servicio.")
    payload = LLMProviderConfiguration(
        Code=configuration.Code, Name=configuration.Name, Provider=cfg.provider,
        Model=cfg.model, BaseUrl=cfg.base_url or None, APIKey=cfg.api_key or None,
        Temperature=cfg.temperature, MaxOutputTokens=cfg.max_output_tokens,
        TimeoutSeconds=cfg.timeout_seconds, AzureEndpoint=cfg.azure_endpoint or None,
        AzureApiVersion=cfg.azure_api_version or None, AzureDeployment=cfg.azure_deployment or None,
        OpenAIOrganization=cfg.organization or None, OpenAIProject=cfg.project or None,
        UseResponsesAPI=cfg.use_responses_api, StoreResponses=cfg.store_responses,
        MaxRetries=cfg.max_retries, ServiceTier=cfg.service_tier,
        IsDefault=configuration.IsDefault, active=True,
    )
    return _save_llm_provider(request, configuration.Code, payload, create_only=True)


@router.get(
    "/api/v1/agent/llm/providers", response_model=list[LLMProviderConfigurationStored]
)
def get_llm_providers() -> list[LLMProviderConfigurationStored]:
    try:
        return [
            LLMProviderConfigurationStored(**row)
            for row in list_llm_provider_configurations()
        ]
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível consultar os fornecedores."
        ) from exc


@router.delete("/api/v1/agent/llm/providers/{code}")
def deactivate_llm_provider(request: Request, code: str) -> dict[str, str]:
    try:
        changed = deactivate_llm_provider_configuration(code.strip())
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível desativar o fornecedor."
        ) from exc
    if not changed:
        raise HTTPException(status_code=404, detail="A configuração não existe.")
    _clear_cache(request)
    return {"status": "deactivated", "code": code.strip()}


@router.put(
    "/api/v1/agent/solidset/agents/{agent_resource_id}/model",
    response_model=AgentIAModelStored,
)
def configure_agent_model(
    request: Request,
    agent_resource_id: UUID,
    configuration: AgentIAModelConfiguration,
) -> AgentIAModelStored:
    payload = configuration.model_dump()
    payload["ProviderCode"] = payload["ProviderCode"].strip()
    payload["Role"] = payload["Role"].strip()
    try:
        saved = save_agent_model_configuration(agent_resource_id, payload)
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail="A configuração solicitada não foi encontrada."
        ) from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(
            status_code=404, detail="O agente indicado não existe."
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="A configuração do modelo não é válida."
        ) from exc
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível atribuir o modelo ao agente."
        ) from exc
    _clear_cache(request)
    return AgentIAModelStored(**saved)


@router.get("/api/v1/agent/solidset/agents/{agent_resource_id}/model")
def read_agent_model(agent_resource_id: UUID) -> dict[str, Any]:
    try:
        saved = get_agent_model_configurations(agent_resource_id)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível consultar o modelo do agente."
        ) from exc
    if not saved:
        raise HTTPException(
            status_code=404, detail="O agente não tem nenhum SysAgentIAModel ativo."
        )
    return {"IDResource": agent_resource_id, "models": saved}
