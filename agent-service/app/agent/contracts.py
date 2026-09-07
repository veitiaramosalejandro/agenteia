"""Shared contracts for agent context, tools, and knowledge results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class AgentContext:
    """Stable identity and request scope exposed to agent tools."""

    agent_resource_id: Optional[str] = None
    solidset_instance_id: Optional[str] = None
    canal_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Normalized result contract for future non-LangChain tool adapters."""

    content: str
    source: str
    confidence: Optional[float] = None
    verified: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolPolicy:
    """Declarative execution policy for a registered tool."""

    requires_agent_context: bool = False
