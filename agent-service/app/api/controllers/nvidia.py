"""Bounded NVIDIA connectivity test; no persistence or agent side effects."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.llm.providers import NVIDIA_BASE_URL, NVIDIA_MODEL

router = APIRouter(tags=["LLM Providers"])
logger = logging.getLogger(__name__)


class NvidiaTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field("Responde brevemente: ¿qué es una GPU?", min_length=1,
                        max_length=8000, pattern=r"\S")
    temperature: float = Field(1, ge=0, le=2)
    top_p: float = Field(0.95, gt=0, le=1)
    max_tokens: int = Field(256, ge=1, le=16384)
    enable_thinking: bool = False


class NvidiaTestResponse(BaseModel):
    provider: str = "nvidia"
    model: str
    content: str
    finish_reason: str | None = None
    usage: dict[str, int | None] | None = None


@router.post("/api/v1/agent/llm/nvidia/test", response_model=NvidiaTestResponse,
             summary="Probar NVIDIA Nemotron sin guardar ni asignar un proveedor")
async def test_nvidia(body: NvidiaTestRequest) -> NvidiaTestResponse:
    if not settings.NVIDIA_API_KEY.strip():
        raise HTTPException(503, "Configura NVIDIA_API_KEY en el entorno del servicio.")
    try:
        # A total deadline also bounds connection setup and response processing.
        async with asyncio.timeout(60):
            async with AsyncOpenAI(api_key=settings.NVIDIA_API_KEY,
                                   base_url=NVIDIA_BASE_URL, timeout=55,
                                   max_retries=0) as client:
                result = await client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=[{"role": "user", "content": body.prompt}],
                    temperature=body.temperature, top_p=body.top_p,
                    max_tokens=body.max_tokens, stream=False,
                    extra_body={"chat_template_kwargs": {
                        "enable_thinking": body.enable_thinking}},
                )
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
