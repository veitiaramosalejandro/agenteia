"""SQL tool adapter for deterministic agent operations."""

from __future__ import annotations

from typing import Any


class AgentSql:
    """Keep SQL and schema tool invocation outside the agent runtime."""

    def __init__(self, query_tool: Any, schema_tool: Any):
        self.query_tool = query_tool
        self.schema_tool = schema_tool

    def query(self, arguments: dict[str, Any]) -> Any:
        return self.query_tool.invoke(dict(arguments))

    def schema(self, arguments: dict[str, Any]) -> Any:
        return self.schema_tool.invoke(dict(arguments))
