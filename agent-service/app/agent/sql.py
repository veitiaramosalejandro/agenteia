"""SQL tool adapter for deterministic agent operations."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_tool_permissions: ContextVar[set[str] | None] = ContextVar(
    "agent_tool_permissions", default=None
)


def set_tool_permissions(permissions: Any) -> Token:
    normalized = None if permissions is None else {
        str(value).strip().lower() for value in permissions if str(value).strip()
    }
    return _tool_permissions.set(normalized)


def reset_tool_permissions(token: Token) -> None:
    _tool_permissions.reset(token)


class AgentSql:
    """Keep SQL and schema tool invocation outside the agent runtime."""

    def __init__(self, query_tool: Any, schema_tool: Any):
        self.query_tool = query_tool
        self.schema_tool = schema_tool

    def query(self, arguments: dict[str, Any]) -> Any:
        self._require("solidset_sql")
        return self.query_tool.invoke(dict(arguments))

    def schema(self, arguments: dict[str, Any]) -> Any:
        self._require("solidset_schema")
        return self.schema_tool.invoke(dict(arguments))

    @staticmethod
    def _require(permission: str) -> None:
        permissions = _tool_permissions.get()
        if permissions is not None and permission not in permissions:
            raise PermissionError(f"Permission required for SQL tool: {permission}")
