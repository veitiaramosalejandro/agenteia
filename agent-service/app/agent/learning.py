"""Central learning coordinator for tool results."""

from __future__ import annotations

import json
from typing import Any

from app.agent.contracts import AgentContext


class AgentLearning:
    """Dispatch learnable tool results without changing their user-facing content."""

    def learn(self, tool_name: str, content: Any, context: AgentContext | None) -> bool:
        if tool_name != "google_web_search" or context is None:
            return False
        try:
            payload = json.loads(str(content))
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        results = payload.get("results") if isinstance(payload, dict) else None
        query = str(payload.get("query") or "").strip() if isinstance(payload, dict) else ""
        if not query or not isinstance(results, list) or not results:
            return False
        from app.agent.tools import _schedule_web_search_learning

        _schedule_web_search_learning(query, results, context.agent_resource_id)
        return True
