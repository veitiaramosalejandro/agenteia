from typing import Any

import redis
from fastapi import APIRouter, HTTPException, Query

from app.response_queue import AgentResponseQueue
from app.services.response_status import list_by_agent, load, load_by_chat, localize
from app.suggestion_queue import SuggestionQueue


router = APIRouter(prefix="/api/v1/agent/responses", tags=["Asynchronous Responses"])
response_queue = AgentResponseQueue()
suggestion_queue = SuggestionQueue()


@router.get("/status")
def read_agent_response_status_by_chat(
    chatId: str = Query(...),
    lang: str | None = Query(None, pattern="^(es|en|pt)$"),
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


@router.get("/by-agent")
def read_agent_response_history(
    agentResourceId: str = Query(..., min_length=1),
    agentIdentityId: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    lang: str | None = Query(None, pattern="^(es|en|pt)$"),
) -> dict[str, Any]:
    resource_ids = [agentResourceId.strip()]
    if agentIdentityId and agentIdentityId.strip() not in resource_ids:
        resource_ids.append(agentIdentityId.strip())
    states_by_request = {
        item["requestId"]: item
        for resource_id in resource_ids
        for item in list_by_agent(resource_id, limit)
    }
    states = sorted(
        states_by_request.values(),
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )[:limit]
    return {
        "agentResourceId": agentResourceId.strip(),
        "total": len(states),
        "items": [localize(item, lang) for item in states],
    }


@router.get("/{request_id}/status")
def read_agent_response_status(
    request_id: str,
    lang: str | None = Query(None, pattern="^(es|en|pt)$"),
) -> dict[str, Any]:
    data = load(request_id.strip())
    if data is None:
        raise HTTPException(status_code=404, detail="O pedido não existe ou expirou.")
    return localize(data, lang)
