from __future__ import annotations

import asyncio
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Body, HTTPException, Request, status

from app.api.schemas.common import (
    ChatQuestionSuggestionItem,
    ChatQuestionSuggestionResponse,
    FrameworkMessageDTO,
)
from app.api.examples import _CHAT_QUESTION_SUGGESTION_EXAMPLES
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


@router.post(
    "/api/v1/agent/notification/chat-question/suggest-response",
    response_model=ChatQuestionSuggestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Suggest a response to a quoted SolidSET chat message",
    responses={
        202: {"description": "Suggestion accepted into the durable processing queue."},
        404: {"description": "The requester's own AI agent is not active."},
        422: {"description": "The FrameworkMessage lacks required chat context."},
        503: {"description": "A database or model dependency is unavailable."},
    },
)
async def suggest_chat_question_response(
    message: Annotated[
        FrameworkMessageDTO,
        Body(openapi_examples=_CHAT_QUESTION_SUGGESTION_EXAMPLES),
    ],
    request: Request,
) -> ChatQuestionSuggestionResponse:
    """Accepts quickly; durable workers publish the result through status."""
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
        if existing and existing.get("status") in {
            "queued",
            "processing",
            "searching",
            "thinking",
            "completed",
        }:
            result = existing.get("result") or {}
            return ChatQuestionSuggestionResponse(
                requestId=request_id,
                questionChatId=str(
                    result.get("questionChatId")
                    or context["quoted_chat_id"]
                    or request_id
                ),
                status=str(existing.get("status") or "queued"),
                code=int(existing.get("code") or 0),
                language=str(result.get("language") or "pt"),
                title=result.get("title"),
                suggestions=[
                    ChatQuestionSuggestionItem(**item)
                    for item in result.get("suggestions") or []
                ],
                statusUrl=f"/api/v1/agent/responses/{request_id}/status",
            )
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
    return ChatQuestionSuggestionResponse(
        requestId=request_id,
        questionChatId=context["quoted_chat_id"] or request_id,
        status="queued",
        code=_RESPONSE_STATUS_CODES["queued"],
        language="pt",
        title=None,
        suggestions=[],
        statusUrl=f"/api/v1/agent/responses/{request_id}/status",
    )
