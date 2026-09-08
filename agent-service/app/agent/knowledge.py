"""Knowledge-source adapter used by the agent runtime."""

from __future__ import annotations

from typing import Any, Optional


class AgentKnowledge:
    """Expose knowledge operations without coupling the runtime to storage details."""

    def __init__(self, backend: Any):
        self.backend = backend

    def search_agent(
        self,
        query: str,
        *,
        agent_resource_id: str,
        canal_id: Optional[str] = None,
        min_score: float = 0.0,
    ) -> str:
        return self.backend.consultar_conocimiento_agente(
            query,
            agent_resource_id=agent_resource_id,
            canal_id=canal_id,
            min_score=min_score,
        )

    def search_documentation(
        self,
        query: str,
        *,
        agent_resource_id: Optional[str] = None,
        canal_id: Optional[str] = None,
        min_score: float = 0.0,
    ) -> str:
        return self.backend.consultar_documentacion(
            query,
            agent_resource_id=agent_resource_id,
            canal_id=canal_id,
            min_score=min_score,
        )

    def search_web_memory(
        self,
        query: str,
        *,
        agent_resource_id: Optional[str] = None,
        limit: int = 5,
    ) -> str:
        return self.backend.consultar_investigacion_web_reciente(
            query,
            agent_resource_id=agent_resource_id,
            limit=limit,
        )

    def search_system_snapshot(
        self,
        query: str,
        *,
        solidset_instance_id: str,
        agent_resource_id: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> str:
        return self.backend.consultar_conocimiento_sistema(
            query,
            solidset_instance_id=solidset_instance_id,
            agent_resource_id=agent_resource_id,
            limit=limit,
            min_score=min_score,
        )
