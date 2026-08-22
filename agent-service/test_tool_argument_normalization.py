import json
import unittest
from unittest.mock import patch

from app.agent.core import MachiningAgent
from app.agent.tools import get_db_schema


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

    def test_unrelated_count_does_not_trigger_resource_sql_query(self):
        term = self.agent._extract_resource_count_term(
            "Cuantas champion tiene el Real Madrid ganadas?"
        )
        self.assertIsNone(term)

    def test_global_resource_count_keeps_direct_sql_query(self):
        term = self.agent._extract_resource_count_term("¿Cuántos recursos hay?")
        self.assertEqual(term, "")

    def test_meeting_resource_count_uses_contextual_sql_route(self):
        term = self.agent._extract_resource_count_term(
            "¿Cuántos recursos tiene este meeting activo?"
        )
        self.assertIsNone(term)

    @patch("app.agent.core.query_sql_server")
    def test_meeting_resource_count_uses_current_meeting_id(self, query_tool):
        query_tool.invoke.return_value = (
            '[{"IDMeeting":"7d7a581d-d7c1-4e18-a11b-6d322e4755c6",'
            '"IDResourceCreator":"resource-1","IDResource":"resource-1",'
            '"ResourceName":"Alejandro"},{"IDResourceCreator":"resource-1",'
            '"IDResource":"resource-2","ResourceName":"Victor"},'
            '{"IDResourceCreator":"resource-1","IDResource":"resource-3",'
            '"ResourceName":"Paulo"},{"IDResourceCreator":"resource-1",'
            '"IDResource":"resource-4","ResourceName":"Ana"}]'
        )
        response = self.agent._resolve_meeting_resource_count_from_db(
            "¿Cuántos recursos tiene este meeting activo?",
            "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
        )
        self.assertEqual("Este meeting activo tiene **4 recursos participantes**.", response)
        sql = query_tool.invoke.call_args.args[0]["query"]
        self.assertIn("dbo.SysMeeting2Resource", sql)
        self.assertIn("dbo.SysResources", sql)
        self.assertIn("7d7a581d-d7c1-4e18-a11b-6d322e4755c6", sql)

    @patch("app.agent.core.query_sql_server")
    def test_meeting_active_resources_are_listed_from_payload_context(self, query_tool):
        query_tool.invoke.return_value = (
            '[{"IDResourceCreator":"resource-1","IDResource":"resource-1",'
            '"ResourceName":"Alejandro"},{"IDResourceCreator":"resource-1",'
            '"IDResource":"resource-2","ResourceName":"Victor"}]'
        )
        response = self.agent._resolve_meeting_resource_count_from_db(
            "Dime cuales son los recursos activos?",
            "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
        )
        self.assertIn("son **2**", response)
        self.assertIn("- Alejandro", response)
        self.assertIn("- Victor", response)

    @patch("app.agent.core.query_sql_server")
    def test_misspelled_spanish_participant_question_stays_in_spanish(self, query_tool):
        query_tool.invoke.return_value = (
            '[{"IDResourceCreator":"resource-1","IDResource":"resource-1",'
            '"ResourceName":"Alejandro"}]'
        )
        response = self.agent._resolve_meeting_resource_count_from_db(
            "Dime los participanten del meeting?",
            "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
        )
        self.assertIn("Los recursos participantes activos", response)

    @patch("app.agent.core.query_sql_server")
    def test_meeting_creator_is_resolved_from_participants(self, query_tool):
        query_tool.invoke.return_value = (
            '[{"IDResourceCreator":"resource-1","IDResource":"resource-1",'
            '"ResourceName":"Alejandro"},{"IDResourceCreator":"resource-1",'
            '"IDResource":"resource-2","ResourceName":"Victor"}]'
        )
        response = self.agent._resolve_meeting_resource_count_from_db(
            "Quien es el creador?",
            "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
        )
        self.assertEqual("El creador de este meeting es **Alejandro**.", response)

    @patch("app.agent.core.query_sql_server")
    def test_meeting_query_failure_does_not_fall_back_to_hallucination(self, query_tool):
        query_tool.invoke.return_value = "Error: SolidSET Data API indisponível"
        response = self.agent._resolve_meeting_resource_count_from_db(
            "¿Quiénes son los participantes?",
            "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
        )
        self.assertEqual(
            "No pude consultar los participantes del meeting en este momento.",
            response,
        )

    def test_channel_names_question_uses_direct_sql_route(self):
        self.assertTrue(
            self.agent._is_channel_names_intent(
                "Dime los nombre de los canales existente en el sistema SOLIDSET?"
            )
        )

    def test_business_entities_are_forced_to_internal_knowledge_route(self):
        examples = (
            "¿Quiénes son los participantes?",
            "Lista los recursos activos",
            "Show the channel tasks",
            "Quais são as atividades?",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(self.agent._is_business_knowledge_query(text))
                self.assertTrue(self.agent._is_internal_domain_query(text))

    def test_live_meeting_participants_require_sql_even_with_vector_context(self):
        meeting_id = "7d7a581d-d7c1-4e18-a11b-6d322e4755c6"
        self.assertTrue(
            self.agent._requires_live_business_data(
                "Dime los nombres de los participantes", meeting_id
            )
        )
        self.assertTrue(
            self.agent._requires_live_business_data(
                "¿Cuántos recursos tiene este meeting activo?", meeting_id
            )
        )

    def test_descriptive_business_question_can_use_vector_knowledge(self):
        self.assertFalse(
            self.agent._requires_live_business_data(
                "Explica qué es un canal de SolidSET"
            )
        )

    def test_dynamic_schema_hints_are_limited_to_business_entities(self):
        self.assertEqual(
            ["SysMeeting", "SysMeeting2Resource", "SysResources"],
            self.agent._business_schema_table_hints(
                "Dime los participantes del meeting"
            ),
        )
        self.assertEqual(
            ["SysTask"],
            self.agent._business_schema_table_hints("Lista las tareas abiertas"),
        )

    def test_dynamic_sql_parameters_are_normalized_as_json(self):
        args, error = self.agent._normalize_tool_args(
            "query_sql_server",
            {"query": "SELECT * FROM dbo.SysTask WHERE ID=%s", "parameters_json": ["id-1"]},
            user_text="Busca la tarea",
            user_id=None,
            canal_id=None,
        )
        self.assertIsNone(error)
        self.assertEqual('["id-1"]', args["parameters_json"])

    @patch("app.agent.tools.read_schema_catalog")
    @patch("app.agent.tools.get_solidset_schema_snapshot")
    @patch("app.agent.tools.current_instance")
    def test_schema_tool_uses_postgres_snapshot_before_data_api(
        self, current, snapshot, read_catalog
    ):
        current.return_value = {"ID": "instance-1", "DataAPI": {"active": True}}
        snapshot.return_value = {"Catalog": {
            "databaseName": "ISIFrameIsicom",
            "tables": [{
                "schemaName": "dbo",
                "tableName": "SysMeeting",
                "columns": [],
                "foreignKeys": [],
            }],
        }}

        result = json.loads(get_db_schema.invoke({"table_name": "SysMeeting"}))

        self.assertEqual("SysMeeting", result["tables"][0]["tableName"])
        read_catalog.assert_not_called()

    def test_channel_summary_followup_uses_direct_summary_route(self):
        self.assertTrue(
            self.agent._is_channel_summary_intent(
                "Si, necesito un resumen del contexto de la conversación del canal actual"
            )
        )

    def test_latest_message_list_without_channel_word_uses_direct_sql_route(self):
        self.assertTrue(
            self.agent._is_last_chat_message_intent(
                "Dame los 5 últimos mensajes que no sean respuestas o preguntas para el agente"
            )
        )

    def test_detects_agent_dialogue_exclusion(self):
        self.assertTrue(
            self.agent._requests_excluding_agent_dialogue(
                "Dame los 5 últimos mensajes que no sean respuestas o preguntas para el agente"
            )
        )

    def test_agent_dialogue_filter_uses_sender_and_message_addressing(self):
        with patch("app.agent.core.settings.SOLIDSET_LOGIN_RESOURCE_ID", "agent-resource"):
            self.assertTrue(self.agent._is_agent_dialogue_message({"sender_resource_id": "agent-resource"}))
            self.assertTrue(self.agent._is_agent_dialogue_message({"message": "Agente, necesito ayuda"}))
            self.assertFalse(self.agent._is_agent_dialogue_message({"message": "Os 3 pontinhos ficaram muito bem"}))

    def test_channel_summary_message_limit_is_clamped(self):
        self.assertEqual(self.agent._channel_summary_limit("resume el canal"), 30)
        self.assertEqual(self.agent._channel_summary_limit("resume 10 mensajes del canal"), 30)
        self.assertEqual(self.agent._channel_summary_limit("resume 500 mensajes del canal"), 500)
        self.assertEqual(self.agent._channel_summary_limit("resume 900 mensajes del canal"), 500)
        self.assertEqual(self.agent._channel_summary_limit("resume 80 mensagens do canal"), 80)
        self.assertEqual(self.agent._channel_summary_limit("summarize 100 messages from the channel"), 100)
        with patch("app.agent.core.settings.CHANNEL_SUMMARY_MAX_MESSAGE_LIMIT", 100):
            self.assertEqual(self.agent._channel_summary_limit("resume 500 mensajes del canal"), 100)

    def test_extracts_participant_for_channel_frequency(self):
        name = self.agent._extract_channel_participant_frequency_name(
            "Agente, el Sr. Paulo Ferreira como actual en el canal, con que frecuenta realiza intervenciones?"
        )
        self.assertEqual(name, "Paulo Ferreira")

    def test_extracts_portuguese_participant_analysis(self):
        name = self.agent._extract_channel_participant_analysis_name(
            "Agente, por favor, faça um resumo das intervenções de Paulo Ferreira no canal "
            "e forneça uma análise com base nas suas respostas."
        )
        self.assertEqual(name, "Paulo Ferreira")

    def test_detects_supported_languages(self):
        self.assertEqual(self.agent._detect_user_language("¿Qué usuarios existen en el canal?"), "es")
        self.assertEqual(self.agent._detect_user_language("Faça um resumo das intervenções no canal"), "pt")
        self.assertEqual(self.agent._detect_user_language("Please summarize the channel messages"), "en")

    def test_multilingual_channel_name_intent(self):
        self.assertTrue(self.agent._is_channel_names_intent("Quais são os nomes dos canais?"))
        self.assertTrue(self.agent._is_channel_names_intent("List the channel names"))

    def test_multilingual_resource_count_intent(self):
        self.assertEqual(self.agent._extract_resource_count_term("Quantos recursos Dev existem?"), "Dev")
        self.assertEqual(self.agent._extract_resource_count_term("How many Dev users are in the system?"), "Dev")


if __name__ == "__main__":
    unittest.main()
