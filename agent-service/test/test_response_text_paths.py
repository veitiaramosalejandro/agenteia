import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from app.services import suggestions
from app.llm.text import response_text
from app.agent.orchestrator import SolidSETOrchestrator


class ResponseTextPathTests(unittest.TestCase):
    def setUp(self):
        runtime = patch.object(suggestions, "agent", Mock())
        runtime.start()
        self.addCleanup(runtime.stop)

    def test_translation_returns_text_without_provider_envelope(self):
        runtime = Mock()
        runtime._detect_user_language.side_effect = ["fr", "pt"]
        model = Mock()
        model.invoke.return_value = AIMessage(content=[{
            "type": "text", "text": "A OpenAI desenvolve inteligencia artificial.",
            "annotations": [], "id": "internal",
        }])
        runtime.get_llm_for_metadata.return_value = (model, None, None)
        orchestrator = object.__new__(SolidSETOrchestrator)
        orchestrator.agent = runtime
        text = orchestrator._ensure_response_language(
            "O que e a OpenAI?", "OpenAI est une entreprise.",
            {"response_language": "pt"},
        )
        self.assertEqual(text, "A OpenAI desenvolve inteligencia artificial.")

    def test_suggestion_repair_extracts_text_before_json_parsing(self):
        model = Mock()
        model.invoke.return_value = AIMessage(content=[{
            "type": "text", "text": '["OpenAI desarrolla inteligencia artificial."]',
            "annotations": [], "id": "internal-message-id",
        }])
        with patch.object(suggestions.agent, "get_llm_for_metadata", return_value=(
            model, None, SimpleNamespace(provider="openai", model="test")
        )):
            raw = suggestions._repair_chat_question_suggestions(
                "invalid", count=1, language="es", grounding_context="OpenAI",
                metadata={},
            )
        self.assertEqual(suggestions._parse_chat_question_suggestions(raw), [
            "OpenAI desarrolla inteligencia artificial."
        ])

    def test_record_reasoning_extracts_text(self):
        model = Mock()
        model.invoke.return_value = AIMessage(content=[{
            "type": "text", "text": '["Revisar el registro."]', "id": "internal"
        }])
        with patch.object(suggestions.agent, "get_llm_for_metadata", return_value=(
            model, None, SimpleNamespace(provider="openai", model="test")
        )):
            raw = suggestions._reason_about_related_record(
                request_text="Analiza", record_context="Registro",
                research_context="", language="es", metadata={},
            )
        self.assertEqual(raw, '["Revisar el registro."]')

    def test_provider_metadata_never_becomes_visible_text(self):
        response = AIMessage(content=[
            {"type": "reasoning", "text": "private"},
            {"type": "text", "text": "OpenAI est une entreprise.",
             "annotations": [], "id": "msg-private"},
        ])
        self.assertEqual(response_text(response), "OpenAI est une entreprise.")


if __name__ == "__main__":
    unittest.main()
