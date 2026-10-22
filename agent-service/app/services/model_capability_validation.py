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

from app.llm import LLMProviderConfig, create_chat_model
from app.llm.text import response_text


CAPABILITIES = ("general", "structured_json", "tool_calling")
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
                   tools: list[dict[str, Any]] | None = None) -> tuple[Any, float]:
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
    started = time.perf_counter()
    response = httpx.post(
        f"{config.base_url.rstrip('/')}/api/chat",
        json=payload,
        timeout=max(5, config.timeout_seconds),
    )
    response.raise_for_status()
    return response.json(), (time.perf_counter() - started) * 1000


def validate_provider(config: LLMProviderConfig, capabilities: list[str] | None = None) -> dict[str, Any]:
    """Probe a provider and return machine-readable contract results."""
    requested = list(dict.fromkeys(capabilities or list(CAPABILITIES)))
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
                else:
                    payload, latency = _invoke_ollama(config, tools=[{
                        "type": "function",
                        "function": {"name": "capability_probe", "description": "Herramienta de prueba sin efectos", "parameters": {"type": "object", "properties": {}}},
                    }])
                    tool_calls = ((payload.get("message") or {}).get("tool_calls") or [])
                    results.append(_result(capability, bool(tool_calls), detail="El modelo devolvió una llamada de herramienta" if tool_calls else "No devolvió una llamada de herramienta", latency_ms=latency))
            else:
                model = create_chat_model(replace(config, temperature=0, max_output_tokens=128, max_retries=0))
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
                response = model.invoke("Responde a esta prueba de compatibilidad.")
                content = response_text(response).strip()
                if capability == "general":
                    ok, detail = bool(content), "Respuesta de texto recibida" if content else "Respuesta vacía"
                elif capability == "structured_json":
                    parsed = response if isinstance(response, dict) else json.loads(content)
                    ok = isinstance(parsed, dict) and parsed.get("decision") in {"allow", "decline"}
                    detail = "JSON estructurado válido" if ok else "JSON no cumple el esquema"
                else:
                    tool_calls = getattr(response, "tool_calls", None) or []
                    ok, detail = bool(tool_calls), "El modelo devolvió una llamada de herramienta" if tool_calls else "No devolvió una llamada de herramienta"
                results.append(_result(capability, ok, detail=detail))
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            status = "adapter_error" if (
                capability in {"structured_json", "tool_calling"}
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
