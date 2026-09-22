import unittest

from app.agent.orchestrator import SolidSETOrchestrator


class FakeAgent:
    def __init__(self):
        self.calls = []

    @staticmethod
    def _is_external_information_query(text):
        return "tiempo" in text.lower()

    @staticmethod
    def _is_internal_domain_query(text):
        return any(term in text.lower() for term in ("canal", "conversación", "consulta interna"))

    @staticmethod
    def _looks_like_raw_tool_response(text):
        return "status=" in text.lower()

    @staticmethod
    def _detect_user_language(text):
        return "es"

    def analyze_event_with_dialogue(self, **kwargs):
        self.calls.append(kwargs)
        return "respuesta"


class OrchestratorTests(unittest.TestCase):
    def test_empty_suggestion_reaches_bounded_repair_instead_of_generic_fallback(self):
        agent = FakeAgent()
        agent.analyze_event_with_dialogue = lambda **kwargs: ""
        orchestrator = SolidSETOrchestrator(agent)

        response = orchestrator.invoke(
            session_id="suggestion-repair",
            user_text="Implementa Floyd en Java",
            message_metadata={"response_suggestion_mode": True, "chat_id": "504815117"},
            auto_reply_mode=True,
        )

        self.assertEqual(response, "")

    def test_direct_code_suggestion_uses_coding_capability(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)

        orchestrator.invoke(
            session_id="code-suggestion",
            user_text="Implementa el algoritmo Floyd en Java",
            message_metadata={
                "response_suggestion_mode": True,
                "advice_request": True,
            },
            auto_reply_mode=True,
        )

        self.assertEqual(
            agent.calls[0]["message_metadata"]["model_capability"], "coding"
        )

    def test_direct_provider_check_is_not_repeated_by_dialogue_core(self):
        agent = FakeAgent()
        agent.direct_calls = 0

        def answer_with_assigned_openai(user_text, metadata, session_id):
            agent.direct_calls += 1
            return None

        agent.answer_with_assigned_openai = answer_with_assigned_openai
        orchestrator = SolidSETOrchestrator(agent)

        orchestrator.invoke(session_id="direct-once", user_text="Explica este código")

        self.assertEqual(agent.direct_calls, 1)
        self.assertTrue(
            agent.calls[0]["message_metadata"]["_assigned_direct_prechecked"]
        )

    def test_external_query_uses_web_route(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)
        response = orchestrator.invoke(
            session_id="s1",
            user_text="¿Qué tiempo hará en Leiria?",
        )
        self.assertEqual(response, "respuesta")
        self.assertEqual(agent.calls[0]["tool_allowlist"], {"google_web_search"})
        self.assertTrue(agent.calls[0]["external_query_mode"])

    def test_unknown_informational_topic_defaults_to_web(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)
        orchestrator.invoke(
            session_id="s-general",
            user_text="Explícame los últimos avances en baterías de estado sólido",
        )
        self.assertEqual(agent.calls[0]["tool_allowlist"], {"google_web_search"})
        self.assertTrue(agent.calls[0]["external_query_mode"])

    def test_ambiguous_agent_question_stays_internal_in_auto_reply(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)
        orchestrator.invoke(
            session_id="s-agent",
            user_text="¿Qué cambió este mes?",
            auto_reply_mode=True,
            tool_allowlist={"query_sql_server", "get_db_schema"},
        )
        self.assertEqual(
            agent.calls[0]["tool_allowlist"],
            {"query_sql_server", "get_db_schema"},
        )
        self.assertFalse(agent.calls[0]["external_query_mode"])

    def test_internal_person_question_never_escapes_to_web_in_auto_reply(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)
        orchestrator.invoke(
            session_id="s-person",
            user_text="Información de Paulo?",
            auto_reply_mode=True,
            tool_allowlist={"query_sql_server", "get_db_schema"},
        )
        self.assertFalse(agent.calls[0]["external_query_mode"])

    def test_work_query_preserves_requested_tools(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)
        response = orchestrator.invoke(
            session_id="s2",
            user_text="Resume el canal actual",
            tool_allowlist={"query_sql_server", "get_db_schema"},
            auto_reply_mode=True,
        )
        self.assertEqual(response, "respuesta")
        self.assertEqual(
            agent.calls[0]["tool_allowlist"],
            {"query_sql_server", "get_db_schema"},
        )
        self.assertFalse(agent.calls[0]["external_query_mode"])

    def test_meeting_context_is_forwarded_to_agent(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)

        orchestrator.invoke(
            session_id="meeting-session",
            user_text="Resume esta conversación",
            meeting_id="meeting-123",
            meeting_code="M8",
            message_kind="ChatMessageMeetingComment",
            message_category="meeting",
            message_metadata={"chat_id": 1819689, "recipient_count": 2, "importance": 1},
        )

        self.assertEqual(agent.calls[0]["meeting_id"], "meeting-123")
        self.assertEqual(agent.calls[0]["meeting_code"], "M8")
        self.assertEqual(agent.calls[0]["message_kind"], "ChatMessageMeetingComment")
        self.assertEqual(agent.calls[0]["message_category"], "meeting")
        self.assertEqual(agent.calls[0]["message_metadata"]["chat_id"], 1819689)

    def test_validation_blocks_raw_tool_payload(self):
        agent = FakeAgent()
        agent.analyze_event_with_dialogue = lambda **kwargs: "status=200; body={...}"
        orchestrator = SolidSETOrchestrator(agent)
        response = orchestrator.invoke(session_id="s3", user_text="Consulta interna")
        self.assertNotIn("status=", response)

    def test_sql_question_selects_coding_capability(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)
        orchestrator.invoke(session_id="sql", user_text="Crea una consulta SQL para el canal")
        self.assertEqual(agent.calls[0]["message_metadata"]["model_capability"], "coding")

    def test_analysis_question_selects_reasoning_capability(self):
        agent = FakeAgent()
        orchestrator = SolidSETOrchestrator(agent)
        orchestrator.invoke(session_id="reason", user_text="Analiza la causa raíz del problema")
        self.assertEqual(agent.calls[0]["message_metadata"]["model_capability"], "reasoning")


if __name__ == "__main__":
    unittest.main()
