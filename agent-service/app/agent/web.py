"""External-web adapter for the agent runtime."""

from __future__ import annotations

from typing import Any, Optional

from app.agent.contracts import AgentContext


class AgentWeb:
    """Keep web-tool invocation and agent scoping outside the core runtime."""

    def __init__(self, tool: Any, learner: Any = None):
        self.tool = tool
        self.learner = learner

    def search(
        self,
        query: str,
        *,
        agent_resource_id: Optional[str] = None,
        tool_permissions: Any = None,
    ) -> Any:
        permissions = None if tool_permissions is None else set(tool_permissions)
        if permissions is not None and "external_web" not in permissions:
            raise PermissionError("Permission required for tool google_web_search: external_web")
        context = AgentContext(
            agent_resource_id=agent_resource_id,
            metadata={"tool_permissions": permissions} if permissions is not None else {},
        )
        result = self.tool.invoke(
            {"query": query},
            config={
                "configurable": {
                    "agent_resource_id": context.agent_resource_id,
                    "learning_managed": self.learner is not None,
                }
            },
        )
        if self.learner is not None:
            self.learner.learn("google_web_search", result, context)
        return result
