"""External-web adapter for the agent runtime."""

from __future__ import annotations

from typing import Any, Optional

from app.agent.contracts import AgentContext


class AgentWeb:
    """Keep web-tool invocation and agent scoping outside the core runtime."""

    def __init__(self, tool: Any, learner: Any = None):
        self.tool = tool
        self.learner = learner

    def search(self, query: str, *, agent_resource_id: Optional[str] = None) -> Any:
        context = AgentContext(agent_resource_id=agent_resource_id)
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
