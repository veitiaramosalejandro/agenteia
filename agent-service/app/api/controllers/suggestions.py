from __future__ import annotations

import asyncio
from time import monotonic
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Body, HTTPException, Request, status

from app.api.schemas.common import (
    ChatQuestionSuggestionItem,
    ChatQuestionSuggestionResponse,
    FrameworkMessageDTO,
)
from app.api.examples import _CHAT_QUESTION_SUGGESTION_EXAMPLES
from app.config import settings
from app.services.instance_resolution import _resolve_request_solidset_instance
from app.services.response_status import CODES as _RESPONSE_STATUS_CODES
from app.services.response_status import create as _create_response_status
from app.services.response_status import load as _load_response_status
from app.services.response_status import update as _update_response_status
from app.services.suggestions import _chat_question_suggestion_context


router = APIRouter(tags=["SolidSET Notifications"])
suggestion_queue = None


def configure(runtime_suggestion_queue: Any) -> None:
    global suggestion_queue
    suggestion_queue = runtime_suggestion_queue


def _completed_response(
    request_id: str, context: dict[str, Any], state: dict[str, Any]
) -> ChatQuestionSuggestionResponse:
    result = state.get("result") or {}
    suggestions = result.get("suggestions") or []
    if not suggestions:
        raise HTTPException(
            status_code=502,
            detail="O worker terminou sem devolver sugestões válidas.",
        )
    return ChatQuestionSuggestionResponse(
        requestId=request_id,
        questionChatId=str(
            result.get("questionChatId")
            or context["quoted_chat_id"]
            or request_id
        ),
        status="completed",
        code=_RESPONSE_STATUS_CODES["completed"],
        language=str(result.get("language") or "pt"),
        title=result.get("title"),
        suggestions=[ChatQuestionSuggestionItem(**item) for item in suggestions],
        statusUrl=f"/api/v1/agent/responses/{request_id}/status",
    )


async def _wait_for_worker_result(
    request_id: str, context: dict[str, Any]
) -> ChatQuestionSuggestionResponse:
    """Espera sin bloquear el event loop mientras un worker procesa la cola."""
    deadline = monotonic() + settings.SUGGESTION_RESPONSE_WAIT_TIMEOUT_SECONDS
    while monotonic() < deadline:
        state = await asyncio.to_thread(_load_response_status, request_id)
        status_name = str((state or {}).get("status") or "")
        if status_name == "completed":
            return _completed_response(request_id, context, state or {})
        if status_name in {"failed", "cancelled"}:
            detail = str((state or {}).get("error") or "").strip()
            failure_status = (state.get("result") or {}).get("httpStatus", 503)
            if failure_status not in {400, 404, 422, 502, 503}:
                failure_status = 503
            raise HTTPException(
                status_code=failure_status if status_name == "failed" else 409,
                detail=detail or "O worker não conseguiu gerar sugestões.",
            )
        await asyncio.sleep(settings.SUGGESTION_RESPONSE_POLL_INTERVAL_SECONDS)
    raise HTTPException(
        status_code=504,
        detail=(
            "O worker não concluiu a sugestão dentro do tempo configurado. "
            f"Consulte /api/v1/agent/responses/{request_id}/status."
        ),
    )


@router.post(
    "/api/v1/agent/notification/chat-question/suggest-response",
    response_model=ChatQuestionSuggestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Suggest a response to a quoted SolidSET chat message",
    responses={
        200: {"description": "The worker completed and returned the suggestions."},
        404: {"description": "The requester's own AI agent is not active."},
        422: {"description": "The FrameworkMessage lacks required chat context."},
        503: {"description": "A database or model dependency is unavailable."},
        504: {"description": "The worker did not finish before the wait timeout."},
    },
)
async def suggest_chat_question_response(
    message: Annotated[
        FrameworkMessageDTO,
        Body(openapi_examples=_CHAT_QUESTION_SUGGESTION_EXAMPLES),
    ],
    request: Request,
) -> ChatQuestionSuggestionResponse:
    """Encola de forma durable y espera asincrónicamente el resultado del worker."""
    print(message.model_dump_json(indent=2))
    payload = message.model_dump(mode="json")
    context = _chat_question_suggestion_context(payload)
    request_id = context["request_id"]
    if (
        not request_id
        or not context["requester_resource"]
        or not context["workroom_id"]
    ):
        raise HTTPException(
            status_code=422, detail="O pedido não contém identidade e chat válidos."
        )
    try:
        instance = _resolve_request_solidset_instance(request)
        if not instance:
            raise HTTPException(
                status_code=400, detail="Instância SolidSET desconhecida."
            )
        existing = _load_response_status(request_id)
        if not existing or existing.get("status") not in {
            "queued", "processing", "searching", "thinking", "completed"
        }:
            _create_response_status(request_id, request_id, 1)
            await asyncio.to_thread(
                suggestion_queue.enqueue, request_id, payload, dict(instance)
            )
    except redis.RedisError as exc:
        _update_response_status(request_id, "failed", error=str(exc))
        raise HTTPException(
            status_code=503, detail="A fila de sugestões não está disponível."
        ) from exc
    print(f"📥 Sugestão enfileirada requestId={request_id}", flush=True)
    return await _wait_for_worker_result(request_id, context)
