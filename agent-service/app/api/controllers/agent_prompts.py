from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.schemas.agent_prompts import (
    AgentPromptBulkResponse,
    AgentPromptGenerateRequest,
    AgentPromptStoredResponse,
)
from app.services.agent_prompt_service import (
    AgentPromptNotFound,
    AgentPromptPersistenceError,
    generate_active_prompt_drafts,
    generate_prompt_draft,
    publish_prompt_draft,
)


router = APIRouter(prefix="/api/v1/agent/solidset/agents", tags=["SolidSET Agents"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentPromptNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=503, detail=str(exc))


@router.post(
    "/prompts/generate",
    response_model=AgentPromptBulkResponse,
    summary="Generate prompt drafts for every active agent in one SolidSET instance",
)
def generate_active_agent_prompts(
    request: AgentPromptGenerateRequest,
    instanceCode: str = Query(..., min_length=1),
) -> AgentPromptBulkResponse:
    try:
        return generate_active_prompt_drafts(instanceCode, request)
    except (AgentPromptNotFound, AgentPromptPersistenceError) as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{agent_resource_id}/prompt/generate",
    response_model=AgentPromptStoredResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a versioned prompt draft from the synchronized SolidSET profile",
)
def generate_agent_prompt(
    agent_resource_id: UUID,
    request: AgentPromptGenerateRequest,
    instanceCode: str = Query(..., min_length=1),
) -> AgentPromptStoredResponse:
    try:
        return AgentPromptStoredResponse(
            **generate_prompt_draft(instanceCode, agent_resource_id, request)
        )
    except (AgentPromptNotFound, AgentPromptPersistenceError) as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{agent_resource_id}/prompt/{prompt_id}/publish",
    response_model=AgentPromptStoredResponse,
    summary="Publish one prompt draft and retire the previous active version",
)
def publish_generated_agent_prompt(
    request: Request,
    agent_resource_id: UUID,
    prompt_id: UUID,
    instanceCode: str = Query(..., min_length=1),
) -> AgentPromptStoredResponse:
    try:
        saved, instance_id = publish_prompt_draft(
            instanceCode, agent_resource_id, prompt_id
        )
        runtime_agent = getattr(request.app.state, "agent", None)
        if runtime_agent is not None:
            runtime_agent.agent_prompt_cache.pop(
                (instance_id, str(agent_resource_id)), None
            )
        return AgentPromptStoredResponse(**saved)
    except (AgentPromptNotFound, AgentPromptPersistenceError) as exc:
        raise _http_error(exc) from exc
