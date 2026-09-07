"""Tool registry compatible with the existing LangChain tool map."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.agent.contracts import AgentContext, ToolPolicy, ToolResult


class ToolRegistry(dict[str, Any]):
    """Named tool collection preserving the current dict-based public behavior."""

    def __init__(self, tools: Mapping[str, Any] | None = None):
        super().__init__(tools or {})
        self._policies: dict[str, ToolPolicy] = {}
        self._learner: Any = None

    def register(self, name: str, tool: Any) -> None:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("Tool name cannot be empty")
        self[normalized] = tool

    def allowed(self, names: Iterable[str] | None = None) -> list[Any]:
        if names is None:
            return list(self.values())
        allowed_names = set(names)
        return [tool for name, tool in self.items() if name in allowed_names]

    def names(self) -> tuple[str, ...]:
        return tuple(self.keys())

    def set_policy(self, name: str, policy: ToolPolicy) -> None:
        if name not in self:
            raise KeyError(name)
        self._policies[name] = policy

    def set_learner(self, learner: Any) -> None:
        self._learner = learner

    def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        context: AgentContext | None = None,
    ) -> Any:
        """Invoke a registered LangChain tool with the scoped agent context."""
        tool = self[name]
        payload = dict(arguments or {})
        policy = self._policies.get(name, ToolPolicy())
        if policy.requires_agent_context and context is not None:
            return tool.invoke(
                payload,
                config={
                    "configurable": {
                        "agent_resource_id": context.agent_resource_id,
                        "learning_managed": policy.learn_result,
                    }
                },
            )
        return tool.invoke(payload)

    def invoke_result(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        context: AgentContext | None = None,
    ) -> ToolResult:
        """Invoke a tool and attach its declarative provenance metadata."""
        raw = self.invoke(name, arguments, context=context)
        policy = self._policies.get(name, ToolPolicy())
        learned = False
        if policy.learn_result and self._learner is not None:
            learned = bool(self._learner.learn(name, raw, context))
        return ToolResult(
            content=str(raw),
            source=policy.source,
            verified=policy.verified,
            metadata={
                "learn_result": policy.learn_result,
                "learning_scope": policy.learning_scope,
                "learning_scheduled": learned,
                "tool_name": name,
            },
        )
