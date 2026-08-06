import unittest

from app.agent.core import MachiningAgent


class ToolArgumentNormalizationTests(unittest.TestCase):
    def setUp(self):
        # Los helpers probados no necesitan arrancar Ollama, Redis, SQL ni Qdrant.
        self.agent = object.__new__(MachiningAgent)

    def test_chat_login_id_is_coerced_before_tool_validation(self):
        args, error = self.agent._normalize_tool_args(
            "solidset_chat_get_messages",
            {"id_login_current": 1, "selected_workrooms_json": []},
            user_text="lee el chat",
            user_id="alejandro.veitia",
            canal_id="canal-123",
        )
        self.assertIsNone(error)
        self.assertEqual(args["id_login_current"], "1")
        self.assertEqual(args["selected_workrooms_json"], ["canal-123"])

    def test_missing_reaction_is_not_invented_or_invoked(self):
        args, error = self.agent._normalize_tool_args(
            "solidset_update_reaction",
            {"id_chat": 1809323, "confirm": True},
            user_text="consulta el tiempo",
            user_id="alejandro.veitia",
            canal_id="canal-123",
        )
        self.assertNotIn("reaction", args)
        self.assertIn("falta el emoji", error)

    def test_context_query_removes_assistant_addressing(self):
        query = self.agent._normalize_context_query(
            "Agente podria decirme cual es pronóstico del tiempo en Leiria?"
        )
        self.assertEqual(query, "cual es pronóstico del tiempo en Leiria?")


if __name__ == "__main__":
    unittest.main()
