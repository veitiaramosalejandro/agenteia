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


if __name__ == "__main__":
    unittest.main()
