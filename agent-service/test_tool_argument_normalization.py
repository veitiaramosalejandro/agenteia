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

    def test_extracts_resource_filter_for_direct_sql_count(self):
        term = self.agent._extract_resource_count_term(
            "Cuantos recurso Dev existe en el sistema SOLIDSET?"
        )
        self.assertEqual(term, "Dev")

    def test_non_count_query_does_not_trigger_direct_resource_query(self):
        term = self.agent._extract_resource_count_term("Explica qué es un recurso Dev")
        self.assertIsNone(term)

    def test_channel_names_question_uses_direct_sql_route(self):
        self.assertTrue(
            self.agent._is_channel_names_intent(
                "Dime los nombre de los canales existente en el sistema SOLIDSET?"
            )
        )

    def test_channel_summary_followup_uses_direct_summary_route(self):
        self.assertTrue(
            self.agent._is_channel_summary_intent(
                "Si, necesito un resumen del contexto de la conversación del canal actual"
            )
        )

    def test_channel_summary_message_limit_is_clamped(self):
        self.assertEqual(self.agent._channel_summary_limit("resume 10 mensajes del canal"), 30)
        self.assertEqual(self.agent._channel_summary_limit("resume 500 mensajes del canal"), 500)
        self.assertEqual(self.agent._channel_summary_limit("resume 900 mensajes del canal"), 500)

    def test_extracts_participant_for_channel_frequency(self):
        name = self.agent._extract_channel_participant_frequency_name(
            "Agente, el Sr. Paulo Ferreira como actual en el canal, con que frecuenta realiza intervenciones?"
        )
        self.assertEqual(name, "Paulo Ferreira")


if __name__ == "__main__":
    unittest.main()
