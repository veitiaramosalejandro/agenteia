"""Interfaz común y adaptadores de proveedores LLM.

Todos los adaptadores devuelven un modelo de chat compatible con LangChain.
El resto del agente solo depende de ``invoke`` y ``bind_tools``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.5
    max_output_tokens: int = 1024
    timeout_seconds: int = 60
    azure_endpoint: str = ""
    azure_api_version: str = ""
    azure_deployment: str = ""


class ChatProvider(ABC):
    """Contrato de creación de modelos utilizado por el agente SolidSET."""

    name: str

    @abstractmethod
    def create_model(self, config: LLMProviderConfig) -> Any:
        """Crea un modelo con ``invoke`` y ``bind_tools``."""


def _optional_class(module_name: str, class_name: str, package_name: str) -> type:
    try:
        module = import_module(module_name)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"El proveedor requiere el paquete opcional '{package_name}'."
        ) from exc


class OllamaProvider(ChatProvider):
    name = "ollama"

    def create_model(self, config: LLMProviderConfig) -> Any:
        ChatOllama = _optional_class("langchain_ollama", "ChatOllama", "langchain-ollama")
        return ChatOllama(
            base_url=config.base_url,
            model=config.model,
            temperature=config.temperature,
            num_predict=config.max_output_tokens,
            top_p=0.9,
            repeat_penalty=1.2,
            client_kwargs={"timeout": config.timeout_seconds},
            async_client_kwargs={"timeout": config.timeout_seconds},
        )


class OpenAIProvider(ChatProvider):
    name = "openai"

    def create_model(self, config: LLMProviderConfig) -> Any:
        ChatOpenAI = _optional_class("langchain_openai", "ChatOpenAI", "langchain-openai")
        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key or None,
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
            "timeout": config.timeout_seconds,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOpenAI(**kwargs)


class OpenAICompatibleProvider(OpenAIProvider):
    name = "openai_compatible"

    def create_model(self, config: LLMProviderConfig) -> Any:
        if not config.base_url:
            raise ValueError("LLM_BASE_URL es obligatoria para openai_compatible.")
        return super().create_model(config)


class AzureOpenAIProvider(ChatProvider):
    name = "azure_openai"

    def create_model(self, config: LLMProviderConfig) -> Any:
        AzureChatOpenAI = _optional_class(
            "langchain_openai", "AzureChatOpenAI", "langchain-openai"
        )
        return AzureChatOpenAI(
            azure_endpoint=config.azure_endpoint or config.base_url,
            azure_deployment=config.azure_deployment or config.model,
            api_version=config.azure_api_version,
            api_key=config.api_key or None,
            temperature=config.temperature,
            max_tokens=config.max_output_tokens,
            timeout=config.timeout_seconds,
        )


class AnthropicProvider(ChatProvider):
    name = "anthropic"

    def create_model(self, config: LLMProviderConfig) -> Any:
        ChatAnthropic = _optional_class(
            "langchain_anthropic", "ChatAnthropic", "langchain-anthropic"
        )
        return ChatAnthropic(
            model=config.model,
            api_key=config.api_key or None,
            temperature=config.temperature,
            max_tokens=config.max_output_tokens,
            timeout=config.timeout_seconds,
        )


class GeminiProvider(ChatProvider):
    name = "gemini"

    def create_model(self, config: LLMProviderConfig) -> Any:
        ChatGoogleGenerativeAI = _optional_class(
            "langchain_google_genai",
            "ChatGoogleGenerativeAI",
            "langchain-google-genai",
        )
        return ChatGoogleGenerativeAI(
            model=config.model,
            google_api_key=config.api_key or None,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
            timeout=config.timeout_seconds,
        )


class ProviderRegistry:
    """Registro extensible; nuevos proveedores no modifican MachiningAgent."""

    _factories: dict[str, Callable[[], ChatProvider]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[[], ChatProvider]) -> None:
        normalized = str(name or "").strip().lower().replace("-", "_")
        if not normalized:
            raise ValueError("El nombre del proveedor no puede estar vacío.")
        cls._factories[normalized] = factory

    @classmethod
    def create(cls, name: str) -> ChatProvider:
        normalized = str(name or "ollama").strip().lower().replace("-", "_")
        factory = cls._factories.get(normalized)
        if factory is None:
            available = ", ".join(sorted(cls._factories))
            raise ValueError(
                f"Proveedor LLM desconocido '{name}'. Disponibles: {available}."
            )
        return factory()

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(sorted(cls._factories))


ProviderRegistry.register("ollama", OllamaProvider)
ProviderRegistry.register("openai", OpenAIProvider)
ProviderRegistry.register("openai_compatible", OpenAICompatibleProvider)
ProviderRegistry.register("local_openai", OpenAICompatibleProvider)
ProviderRegistry.register("azure_openai", AzureOpenAIProvider)
ProviderRegistry.register("anthropic", AnthropicProvider)
ProviderRegistry.register("gemini", GeminiProvider)


def provider_config_from_settings(settings: Any) -> LLMProviderConfig:
    provider = str(getattr(settings, "LLM_PROVIDER", "ollama") or "ollama")
    configured_base = str(getattr(settings, "LLM_BASE_URL", "") or "").strip()
    if provider.strip().lower().replace("-", "_") == "ollama" and not configured_base:
        configured_base = str(getattr(settings, "OLLAMA_BASE_URL", "") or "")
    return LLMProviderConfig(
        provider=provider,
        model=str(getattr(settings, "MODEL_NAME", "") or "").strip(),
        base_url=configured_base,
        api_key=str(getattr(settings, "LLM_API_KEY", "") or ""),
        temperature=float(getattr(settings, "LLM_TEMPERATURE", 0.5)),
        max_output_tokens=int(getattr(settings, "LLM_MAX_OUTPUT_TOKENS", 1024)),
        timeout_seconds=int(getattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 60)),
        azure_endpoint=str(getattr(settings, "AZURE_OPENAI_ENDPOINT", "") or ""),
        azure_api_version=str(getattr(settings, "AZURE_OPENAI_API_VERSION", "") or ""),
        azure_deployment=str(getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or ""),
    )


def provider_config_from_record(record: dict[str, Any]) -> LLMProviderConfig:
    """Convierte una fila privada de PostgreSQL al contrato común."""
    return LLMProviderConfig(
        provider=str(record.get("Provider") or "ollama"),
        model=str(record.get("Model") or "").strip(),
        base_url=str(record.get("BaseUrl") or "").strip(),
        api_key=str(record.get("APIKey") or ""),
        temperature=float(record.get("Temperature", 0.5)),
        max_output_tokens=int(record.get("MaxOutputTokens", 1024)),
        timeout_seconds=int(record.get("TimeoutSeconds", 60)),
        azure_endpoint=str(record.get("AzureEndpoint") or ""),
        azure_api_version=str(record.get("AzureApiVersion") or ""),
        azure_deployment=str(record.get("AzureDeployment") or ""),
    )


def create_chat_model(config: LLMProviderConfig) -> Any:
    if not config.model:
        raise ValueError("MODEL_NAME no puede estar vacío.")
    return ProviderRegistry.create(config.provider).create_model(config)
