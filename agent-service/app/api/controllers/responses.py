from typing import Any

import redis
from fastapi import APIRouter, HTTPException, Query

from app.response_queue import AgentResponseQueue
from app.services.response_status import load, load_by_chat, localize
from app.suggestion_queue import SuggestionQueue


router = APIRouter(prefix="/api/v1/agent/responses", tags=["Asynchronous Responses"])
response_queue = AgentResponseQueue()
suggestion_queue = SuggestionQueue()


@router.get("/status")
def read_agent_response_status_by_chat(
    chatId: str = Query(...),
    lang: str = Query("pt", pattern="^(es|en|pt)$"),
) -> dict[str, Any]:
    data = load_by_chat(str(chatId).strip())
    if data is None:
        raise HTTPException(
            status_code=404, detail="Não existe um estado para esse chatId."
        )
    return localize(data, lang)


@router.get("/queue/status")
def read_agent_response_queue_status() -> dict[str, Any]:
    try:
        return response_queue.stats()
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503, detail="O Redis Stream não está disponível."
        ) from exc


@router.get("/suggestions/queue/status")
def read_suggestion_queue_status() -> dict[str, Any]:
    try:
        return suggestion_queue.stats()
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503, detail="A fila de sugestões não está disponível."
        ) from exc


@router.get("/{request_id}/status")
def read_agent_response_status(
    request_id: str,
    lang: str = Query("pt", pattern="^(es|en|pt)$"),
) -> dict[str, Any]:
    data = load(request_id.strip())
    if data is None:
        raise HTTPException(status_code=404, detail="O pedido não existe ou expirou.")
    return localize(data, lang)
