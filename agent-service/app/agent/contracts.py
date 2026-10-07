"""Shared contracts for agent context, tools, and knowledge results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
class ToolAuditEvent:
    """Minimal execution trace that can be persisted by a host application."""

    tool_name: str
    source: str
    success: bool
    elapsed_seconds: float
    agent_resource_id: Optional[str] = None
    session_id: Optional[str] = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_type: Optional[str] = None


@dataclass(frozen=True)
class ToolPolicy:
    """Declarative execution policy for a registered tool."""

    requires_agent_context: bool = False
    source: str = "tool"
    verified: bool = False
    learn_result: bool = False
    learning_scope: Optional[str] = None
    required_permission: Optional[str] = None
    confidence: Optional[float] = None
