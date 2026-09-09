"""Bounded NVIDIA connectivity test; no persistence or agent side effects."""

import asyncio
import logging
import json
from time import monotonic
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
import httpx

from app.config import settings
from app.llm.providers import NVIDIA_BASE_URL, NVIDIA_MODEL, nvidia_model_options

router = APIRouter(tags=["LLM Providers"])
logger = logging.getLogger(__name__)


class NvidiaTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(NVIDIA_MODEL, min_length=1, max_length=255, pattern=r"^\S+$")

    prompt: str = Field("Responde brevemente: ¿qué es una GPU?", min_length=1,
                        max_length=8000, pattern=r"\S")
    image_url: HttpUrl | None = None
    stream: bool = True
    seed: int = Field(0, ge=-9007199254740991, le=9007199254740991)
    reasoning_effort: Literal["low", "high", "max"] = "max"
    temperature: float = Field(1, ge=0, le=1)
    max_tokens: int = Field(256, ge=1, le=16384)
    timeout_seconds: int = Field(
        180, ge=10, le=600,
        description="Plazo total local (10–600 segundos), incluido todo el stream. No se envía a NVIDIA.",
    )


class NvidiaTestResponse(BaseModel):
    provider: str = "nvidia"
    model: str
    content: str
    finish_reason: str | None = None
    usage: dict[str, int | None] | None = None


def _client(timeout_seconds: int):
    return AsyncOpenAI(
        api_key=settings.NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL,
        timeout=httpx.Timeout(timeout_seconds, connect=10, pool=10), max_retries=0,
    )


def _error_details(exc, timeout_seconds, started, phase):
    if isinstance(exc, APITimeoutError):
        status, code = 504, "upstream_timeout"
        message = "Se agotó la espera de conexión o de datos de NVIDIA."
    elif isinstance(exc, TimeoutError):
        status, code = 504, "request_deadline_exceeded"
        message = f"La petición superó el plazo total de {timeout_seconds} segundos."
    elif isinstance(exc, RateLimitError):
        status, code = 429, "rate_limit"
        message = "NVIDIA ha alcanzado el límite de solicitudes."
    else:
        status, code = 502, "upstream_error"
        message = "No se pudo completar la petición a NVIDIA."
    details = dict(status=status, code=code, message=message, phase=phase,
                   timeout_seconds=timeout_seconds, elapsed_seconds=round(monotonic() - started, 3))
    logger.warning("NVIDIA test failed: code=%s phase=%s elapsed_seconds=%s",
                   code, phase, details["elapsed_seconds"])
    return details


@router.post("/api/v1/agent/llm/nvidia/test", response_model=NvidiaTestResponse,
             summary="Probar un modelo NVIDIA con texto, imágenes y streaming",
             responses={200: {"content": {"text/event-stream": {}}}})
async def test_nvidia(body: NvidiaTestRequest):
    if not settings.NVIDIA_API_KEY.strip():
        raise HTTPException(503, "Configura NVIDIA_API_KEY en el entorno del servicio.")
    content = body.prompt
    if body.image_url:
        content = [{"type": "text", "text": body.prompt},
                   {"type": "image_url", "image_url": {"url": str(body.image_url)}}]
    payload = dict(model=body.model, messages=[{"role": "user", "content": content}],
                   temperature=body.temperature, max_tokens=body.max_tokens,
                   stream=body.stream)
    payload.update(nvidia_model_options(body.model))
    if body.model == "moonshotai/kimi-k3":
        payload.update(seed=body.seed, reasoning_effort=body.reasoning_effort)
    elif {"seed", "reasoning_effort"} & body.model_fields_set:
        raise HTTPException(422, "seed y reasoning_effort solo se admiten para Kimi K3 en esta prueba.")
    if body.stream:
        return StreamingResponse(_stream(payload, body.timeout_seconds), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    started = monotonic()
    try:
        async with asyncio.timeout(body.timeout_seconds):
            async with _client(body.timeout_seconds) as client:
                result = await client.chat.completions.create(**payload)
        if not result.choices:
            raise HTTPException(502, "NVIDIA devolvió una respuesta sin alternativas.")
        choice = result.choices[0]
        return NvidiaTestResponse(
            model=result.model, content=choice.message.content or "",
            finish_reason=choice.finish_reason,
            usage=({"prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "total_tokens": result.usage.total_tokens} if result.usage else None),
        )
    except (TimeoutError, APIError) as exc:
        details = _error_details(exc, body.timeout_seconds, started, "response")
        raise HTTPException(details["status"], details) from exc


async def _stream(payload, timeout_seconds=180):
    """Close upstream on completion, timeout or client cancellation.

    Headers are already sent: upstream failures are sanitized SSE error events.
    """
    started = monotonic()
    phase = "first_chunk"
    try:
        async with asyncio.timeout(timeout_seconds):
            async with _client(timeout_seconds) as client:
                stream = await client.chat.completions.create(**payload)
                async with stream:
                    async for chunk in stream:
                        phase = "stream"
                        yield "data: " + chunk.model_dump_json(exclude_none=True) + "\n\n"
        yield "data: [DONE]\n\n"
    except (TimeoutError, APIError) as exc:
        details = _error_details(exc, timeout_seconds, started, phase)
        yield "event: error\ndata: " + json.dumps(details, ensure_ascii=False) + "\n\n"
