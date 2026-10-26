"""Runtime capability probes for configured LLM providers.

The probes deliberately test provider contracts instead of matching model names.
They are safe: tool probing only asks the model to select a no-op tool and never
executes a returned tool call.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
from langchain_core.messages import HumanMessage

from app.llm import LLMProviderConfig, create_chat_model
from app.llm.text import response_text


CAPABILITIES = ("general", "structured_json", "tool_calling", "vision", "audio")
VISION_PROBE_IMAGE = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+AAAAAIAAUQ0N6YAAAAASUVORK5CYII="
)
AUDIO_PROBE_WAV = "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQIAAAAAAA=="
_DECISION_SCHEMA = {
    "title": "CapabilityDecision",
    "type": "object",
    "properties": {"decision": {"type": "string", "enum": ["allow", "decline"]}},
    "required": ["decision"],
    "additionalProperties": False,
}


def _result(capability: str, ok: bool, *, detail: str = "", latency_ms: float | None = None,
            status: str | None = None) -> dict[str, Any]:
    return {
        "capability": capability,
        "ok": ok,
        "status": status or ("passed" if ok else "failed"),
        "detail": detail,
        "latencyMs": round(latency_ms, 1) if latency_ms is not None else None,
    }


def _ollama_model_available(config: LLMProviderConfig) -> tuple[bool, str]:
    base_url = config.base_url.rstrip("/")
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=max(3, config.timeout_seconds))
        response.raise_for_status()
        models = response.json().get("models") or []
        names = {str(item.get("name") or item.get("model") or "") for item in models}
        if config.model not in names:
            return False, f"El modelo no está disponible en Ollama: {config.model}"
        return True, "Modelo disponible en Ollama"
    except Exception as exc:
        return False, f"No se pudo consultar Ollama: {type(exc).__name__}: {exc}"


def _invoke_ollama(config: LLMProviderConfig, *, schema: dict[str, Any] | None = None,
                   tools: list[dict[str, Any]] | None = None,
                   vision: bool = False) -> tuple[Any, float]:
    import time

    payload: dict[str, Any] = {
        "model": config.model,
        "stream": False,
        # Capability probes measure the provider's public response contract,
        # not its hidden reasoning budget.  Thinking models can otherwise
        # spend all num_predict tokens in `message.thinking` and return an
        # empty `message.content`, producing a false negative for text/JSON.
        "think": False,
        "messages": [{"role": "user", "content": "Responde a esta prueba de compatibilidad."}],
        "options": {"temperature": 0, "num_predict": 128},
    }
    if schema is not None:
        payload["format"] = schema
    if tools is not None:
        payload["tools"] = tools
        payload["messages"] = [{
            "role": "user",
            "content": "Usa la herramienta de prueba para indicar que puedes llamar herramientas.",
        }]
    if vision:
        payload["messages"] = [{
            "role": "user",
            "content": "Describe brevemente la imagen de prueba.",
            "images": [VISION_PROBE_IMAGE],
        }]
    started = time.perf_counter()
    response = httpx.post(
        f"{config.base_url.rstrip('/')}/api/chat",
        json=payload,
        timeout=max(5, config.timeout_seconds),
    )
    response.raise_for_status()
    return response.json(), (time.perf_counter() - started) * 1000


def _invoke_ollama_audio(config: LLMProviderConfig) -> tuple[Any, float]:
    """Send a real audio probe through Ollama's OpenAI-compatible endpoint.

    Ollama's native ``/api/chat`` contract represents multimodal input with
    ``images`` and its older versions reject an ``input_audio`` content part.
    The OpenAI-compatible endpoint is the contract that can carry audio, so
    this probe must use ``/v1/chat/completions``.  A provider response is
    required; metadata or a model-name claim is not enough to mark audio as
    supported.
    """
    import time

    payload = {
        "model": config.model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe brevemente este audio."},
                {"type": "input_audio", "input_audio": {
                    "data": AUDIO_PROBE_WAV,
                    "format": "wav",
                }},
            ],
        }],
        "temperature": 0,
        "max_tokens": 32,
    }
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    started = time.perf_counter()
    response = httpx.post(
        f"{config.base_url.rstrip('/')}/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=max(5, config.timeout_seconds),
    )
    response.raise_for_status()
    return response.json(), (time.perf_counter() - started) * 1000


def _openai_content(payload: Any) -> str:
    """Extract text from an OpenAI-compatible non-streaming response."""
    try:
        content = payload["choices"][0]["message"].get("content")
    except (AttributeError, IndexError, KeyError, TypeError):
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict) and part.get("text")
        ).strip()
    return ""


def validate_provider(config: LLMProviderConfig, capabilities: list[str] | None = None) -> dict[str, Any]:
    """Probe a provider and return machine-readable contract results."""
    aliases = {
        "multimodal": "vision", "image": "vision", "visual": "vision", "imagen": "vision",
        "audio_input": "audio", "speech": "audio",
    }
    requested = list(dict.fromkeys(
        aliases.get(str(item).strip().casefold(), str(item).strip().casefold())
        for item in (capabilities or list(CAPABILITIES))
    ))
    unknown = [item for item in requested if item not in CAPABILITIES]
    if unknown:
        raise ValueError(f"Capacidades no soportadas por el validador: {', '.join(unknown)}")

    results: list[dict[str, Any]] = []
    availability = True
    availability_detail = "Proveedor remoto; se valida durante la prueba"
    if config.provider == "ollama":
        availability, availability_detail = _ollama_model_available(config)
    if not availability:
        return {
            "provider": config.provider,
            "model": config.model,
            "available": False,
            "availableDetail": availability_detail,
            "capabilities": [_result(capability, False, detail=availability_detail, status="unavailable") for capability in requested],
            "passed": False,
        }

    for capability in requested:
        try:
            if config.provider == "ollama":
                if capability == "general":
                    payload, latency = _invoke_ollama(config)
                    content = ((payload.get("message") or {}).get("content") or "").strip()
                    results.append(_result(capability, bool(content), detail="Respuesta de texto recibida" if content else "Respuesta vacía", latency_ms=latency))
                elif capability == "structured_json":
                    payload, latency = _invoke_ollama(config, schema=_DECISION_SCHEMA)
                    content = ((payload.get("message") or {}).get("content") or "").strip()
                    parsed = json.loads(content)
                    ok = isinstance(parsed, dict) and parsed.get("decision") in {"allow", "decline"}
                    results.append(_result(capability, ok, detail="JSON estructurado válido" if ok else "JSON no cumple el esquema", latency_ms=latency))
                elif capability == "vision":
                    payload, latency = _invoke_ollama(config, vision=True)
                    content = ((payload.get("message") or {}).get("content") or "").strip()
                    results.append(_result(
                        capability, bool(content),
                        detail=("Entrada visual aceptada y respuesta recibida" if content
                                else "El modelo no devolvió respuesta para la entrada visual"),
                        latency_ms=latency,
                    ))
                elif capability == "audio":
                    payload, latency = _invoke_ollama_audio(config)
                    content = _openai_content(payload)
                    results.append(_result(
                        capability,
                        bool(content),
                        detail=("Entrada de audio aceptada y respuesta recibida"
                                if content else
                                "El proveedor aceptó la petición, pero devolvió una respuesta vacía"),
                        latency_ms=latency,
                    ))
                else:
                    payload, latency = _invoke_ollama(config, tools=[{
                        "type": "function",
                        "function": {"name": "capability_probe", "description": "Herramienta de prueba sin efectos", "parameters": {"type": "object", "properties": {}}},
                    }])
                    tool_calls = ((payload.get("message") or {}).get("tool_calls") or [])
                    results.append(_result(capability, bool(tool_calls), detail="El modelo devolvió una llamada de herramienta" if tool_calls else "No devolvió una llamada de herramienta", latency_ms=latency))
            else:
                model = create_chat_model(replace(
                    config,
                    temperature=0,
                    max_output_tokens=128,
                    max_retries=0,
                    use_responses_api=False if capability == "audio" else config.use_responses_api,
                ))
                if capability == "structured_json":
                    structured_factory = getattr(model, "with_structured_output", None)
                    if not callable(structured_factory):
                        raise RuntimeError("El adaptador no expone salida estructurada.")
                    model = structured_factory(_DECISION_SCHEMA)
                elif capability == "tool_calling":
                    model = model.bind_tools([{
                        "name": "capability_probe",
                        "description": "Herramienta de prueba sin efectos",
                        "parameters": {"type": "object", "properties": {}},
                    }])
                if capability == "vision":
                    probe_message = HumanMessage(content=[
                        {"type": "text", "text": "Describe brevemente la imagen de prueba."},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{VISION_PROBE_IMAGE}"
                        }},
                    ])
                elif capability == "audio":
                    probe_message = HumanMessage(content=[
                        {"type": "text", "text": "Describe brevemente el audio de prueba."},
                        {"type": "input_audio", "input_audio": {
                            "data": AUDIO_PROBE_WAV, "format": "wav"
                        }},
                    ])
                else:
                    probe_message = "Responde a esta prueba de compatibilidad."
                response = model.invoke([probe_message] if capability in {"vision", "audio"} else probe_message)
                content = response_text(response).strip()
                if capability == "general":
                    ok, detail = bool(content), "Respuesta de texto recibida" if content else "Respuesta vacía"
                elif capability == "structured_json":
                    parsed = response if isinstance(response, dict) else json.loads(content)
                    ok = isinstance(parsed, dict) and parsed.get("decision") in {"allow", "decline"}
                    detail = "JSON estructurado válido" if ok else "JSON no cumple el esquema"
                elif capability in {"vision", "audio"}:
                    ok, detail = bool(content), (
                        ("Entrada visual aceptada y respuesta recibida" if capability == "vision"
                         else "Entrada de audio aceptada y respuesta recibida")
                        if content else
                        ("El modelo no devolvió respuesta para la entrada visual"
                         if capability == "vision"
                         else "El modelo no devolvió respuesta para la entrada de audio")
                    )
                else:
                    tool_calls = getattr(response, "tool_calls", None) or []
                    ok, detail = bool(tool_calls), "El modelo devolvió una llamada de herramienta" if tool_calls else "No devolvió una llamada de herramienta"
                results.append(_result(capability, ok, detail=detail))
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if capability == "audio" and isinstance(exc, httpx.HTTPStatusError):
                # Keep the provider's actual rejection visible.  A 400 while
                # decoding the sample is a failed runtime probe, not proof
                # that the model metadata lacks audio.
                try:
                    body = exc.response.json()
                    provider_error = body.get("error") if isinstance(body, dict) else None
                    if isinstance(provider_error, dict):
                        provider_error = provider_error.get("message") or provider_error
                    if provider_error:
                        detail = f"HTTP {exc.response.status_code}: {provider_error}"
                except (ValueError, TypeError):
                    pass
                status = "unsupported" if any(
                    marker in detail.casefold()
                    for marker in ("not supported", "unsupported", "no soporta")
                ) else "failed"
            else:
                status = "adapter_error" if (
                    capability in {"structured_json", "tool_calling", "vision", "audio"}
                    and not isinstance(exc, json.JSONDecodeError)
                ) else "failed"
            results.append(_result(capability, False, detail=detail, status=status))

    return {
        "provider": config.provider,
        "model": config.model,
        "available": availability,
        "availableDetail": availability_detail,
        "capabilities": results,
        "passed": availability and all(item["ok"] for item in results),
    }
