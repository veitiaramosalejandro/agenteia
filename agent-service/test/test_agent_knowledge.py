import unittest
from unittest.mock import Mock

from app.agent.knowledge import AgentKnowledge


class AgentKnowledgeTests(unittest.TestCase):
    def test_agent_search_preserves_scope_and_threshold(self):
        backend = Mock()
        backend.consultar_conocimiento_agente.return_value = "known"
        knowledge = AgentKnowledge(backend)

        result = knowledge.search_agent(
            "question", agent_resource_id="agent-a", canal_id="channel-a", min_score=0.7
        )

        self.assertEqual(result, "known")
        backend.consultar_conocimiento_agente.assert_called_once_with(
            "question",
            agent_resource_id="agent-a",
            canal_id="channel-a",
            min_score=0.7,
        )

    def test_web_and_system_searches_delegate_their_scopes(self):
        backend = Mock()
        knowledge = AgentKnowledge(backend)

        knowledge.search_web_memory("web", agent_resource_id="agent-a", limit=4)
        knowledge.search_system_snapshot(
            "system",
            solidset_instance_id="instance-a",
            agent_resource_id="agent-a",
            limit=2,
            min_score=0.8,
        )

        backend.consultar_investigacion_web_reciente.assert_called_once_with(
            "web", agent_resource_id="agent-a", limit=4
        )
        backend.consultar_conocimiento_sistema.assert_called_once_with(
            "system",
            solidset_instance_id="instance-a",
            agent_resource_id="agent-a",
            limit=2,
            min_score=0.8,
        )


if __name__ == "__main__":
    unittest.main()
