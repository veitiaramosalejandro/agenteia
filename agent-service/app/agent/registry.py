"""Tool registry compatible with the existing LangChain tool map."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class ToolRegistry(dict[str, Any]):
    """Named tool collection preserving the current dict-based public behavior."""

    def __init__(self, tools: Mapping[str, Any] | None = None):
        super().__init__(tools or {})

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
