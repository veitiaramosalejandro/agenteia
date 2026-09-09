"""Bounded NVIDIA connectivity test; no persistence or agent side effects."""

import asyncio
import logging
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

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


class NvidiaTestResponse(BaseModel):
    provider: str = "nvidia"
    model: str
    content: str
    finish_reason: str | None = None
    usage: dict[str, int | None] | None = None


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
        return StreamingResponse(_stream(payload), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        async with asyncio.timeout(60):
            async with AsyncOpenAI(api_key=settings.NVIDIA_API_KEY,
                                   base_url=NVIDIA_BASE_URL, timeout=55,
                                   max_retries=0) as client:
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
    except (TimeoutError, APITimeoutError) as exc:
        raise HTTPException(504, "NVIDIA excedió el tiempo de espera.") from exc
    except RateLimitError as exc:
        raise HTTPException(429, "NVIDIA ha alcanzado el límite de solicitudes.") from exc
    except APIError as exc:
        logger.warning("NVIDIA test failed: %s", type(exc).__name__)
        raise HTTPException(502, "No se pudo completar la petición a NVIDIA.") from exc


async def _stream(payload):
    """Close upstream on completion, timeout or client cancellation.

    Headers are already sent: upstream failures are sanitized SSE error events.
    """
    try:
        async with asyncio.timeout(60):
            async with AsyncOpenAI(api_key=settings.NVIDIA_API_KEY,
                                   base_url=NVIDIA_BASE_URL, timeout=55,
                                   max_retries=0) as client:
                stream = await client.chat.completions.create(**payload)
                async with stream:
                    async for chunk in stream:
                        yield "data: " + chunk.model_dump_json(exclude_none=True) + "\n\n"
        yield "data: [DONE]\n\n"
    except (TimeoutError, APIError) as exc:
        code = 504 if isinstance(exc, (TimeoutError, APITimeoutError)) else (
            429 if isinstance(exc, RateLimitError) else 502)
        yield "event: error\ndata: " + json.dumps({
            "status": code, "message": "No se pudo completar la petición a NVIDIA."}) + "\n\n"
