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

    def learn_manual(self, content: str, category: str = "general") -> bool:
        """Persist an explicit operator teaching event through the existing tool."""
        return self.learn_manual_result(content, category).startswith("✅")

    def learn_manual_result(self, content: str, category: str = "general") -> str:
        """Persist manual teaching and preserve the tool's user-facing result."""
        text = str(content or "").strip()
        if not text:
            return "Error: el aprendizaje no puede estar vacío"
        from app.agent.tools import learn_new_fact

        return str(learn_new_fact.invoke({
            "fact_description": text,
            "category": str(category or "general").strip() or "general",
        }))
