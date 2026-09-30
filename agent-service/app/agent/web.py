"""External-web adapter for the agent runtime."""

from __future__ import annotations

from typing import Any, Optional

from app.agent.contracts import AgentContext
from app.agent.capabilities import tool_permissions as resolve_tool_permissions


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
        solidset_instance_id: Optional[str] = None,
        tool_permissions: Any = None,
    ) -> Any:
        permissions = None if tool_permissions is None else resolve_tool_permissions(tool_permissions)
        print(
            "AGENT_WEB_PERMISSION_EFFECTIVE "
            f"agent={agent_resource_id or '-'} instance={solidset_instance_id or '-'} "
            f"raw={tool_permissions!r} effective={sorted(permissions) if permissions is not None else None} "
            f"granted={permissions is None or 'external_web' in permissions}",
            flush=True,
        )
        if permissions is not None and "external_web" not in permissions:
            raise PermissionError("Permission required for tool google_web_search: external_web")
        context = AgentContext(
            agent_resource_id=agent_resource_id,
            solidset_instance_id=solidset_instance_id,
            metadata={
                "tool_permissions": permissions,
                "solidset_instance_id": solidset_instance_id,
            },
        )
        result = self.tool.invoke(
            {"query": query},
            config={
                "configurable": {
                    "agent_resource_id": context.agent_resource_id,
                    "solidset_instance_id": solidset_instance_id,
                    "learning_managed": self.learner is not None,
                }
            },
        )
        if self.learner is not None:
            self.learner.learn("google_web_search", result, context)
        return result
