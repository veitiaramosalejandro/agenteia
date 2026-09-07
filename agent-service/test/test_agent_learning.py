import unittest
from unittest.mock import patch

from app.agent.contracts import AgentContext
from app.agent.learning import AgentLearning


class AgentLearningTests(unittest.TestCase):
    @patch("app.agent.tools._schedule_web_search_learning")
    def test_learns_web_results_in_agent_scope(self, schedule):
        payload = {
            "query": "dato actual",
            "results": [{"title": "Fuente", "snippet": "Hecho", "url": "https://example.com"}],
        }

        learned = AgentLearning().learn(
            "google_web_search", __import__("json").dumps(payload), AgentContext(agent_resource_id="agent-a")
        )

        self.assertTrue(learned)
        schedule.assert_called_once_with("dato actual", payload["results"], "agent-a")

    def test_ignores_non_web_or_invalid_results(self):
        learner = AgentLearning()
        context = AgentContext(agent_resource_id="agent-a")

        self.assertFalse(learner.learn("query_sql_server", "rows", context))
        self.assertFalse(learner.learn("google_web_search", "not-json", context))
        self.assertFalse(learner.learn("google_web_search", "{}", None))

    @patch("app.agent.tools.learn_new_fact")
    def test_manual_learning_uses_existing_learning_tool(self, learn_new_fact):
        learn_new_fact.invoke.return_value = "✅ Aprendizaje registrado correctamente"

        self.assertTrue(AgentLearning().learn_manual("Hecho", "operacion"))
        learn_new_fact.invoke.assert_called_once_with({
            "fact_description": "Hecho",
            "category": "operacion",
        })


if __name__ == "__main__":
    unittest.main()
