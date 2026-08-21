import unittest
from unittest.mock import MagicMock

from app.agent.core import MachiningAgent
from app.agent.orchestrator import SolidSETOrchestrator
from app.main import _direct_courtesy_response


class LanguageDetectionTests(unittest.TestCase):
    def setUp(self):
        self.agent = MachiningAgent.__new__(MachiningAgent)

    def test_detects_short_portuguese_greeting(self):
        self.assertEqual("pt", self.agent._detect_user_language("Bom dia"))

    def test_detects_short_english_greeting(self):
        self.assertEqual("en", self.agent._detect_user_language("Good morning"))

    def test_detects_short_spanish_greeting(self):
        self.assertEqual("es", self.agent._detect_user_language("Buenos días"))

    def test_direct_portuguese_greeting_is_localized(self):
        response = _direct_courtesy_response("Bom dia", "Alejandro Veitia")
        self.assertEqual(
            "Olá, Alejandro Veitia! É um prazer cumprimentá-lo. Como posso ajudar?",
            response,
        )

    def test_direct_english_greeting_is_localized(self):
        response = _direct_courtesy_response("Good morning", "Alejandro Veitia")
        self.assertEqual(
            "Hello, Alejandro Veitia! It is a pleasure to greet you. How can I help?",
            response,
        )

    def test_language_normalizer_uses_model_metadata_without_scope_error(self):
        orchestrator = SolidSETOrchestrator.__new__(SolidSETOrchestrator)
        model = MagicMock()
        model.invoke.return_value = MagicMock(content="Bom dia! Como posso ajudar?")
        orchestrator.agent = MagicMock()
        orchestrator.agent._detect_user_language.side_effect = ["pt", "es"]
        orchestrator.agent.get_llm_for_metadata.return_value = (model, None, None)

        response = orchestrator._ensure_response_language(
            "Bom dia",
            "¡Hola! ¿En qué puedo ayudarte?",
            {"agent_resource_id": "resource-1"},
        )

        self.assertEqual("Bom dia! Como posso ajudar?", response)
        orchestrator.agent.get_llm_for_metadata.assert_called_once_with(
            {"agent_resource_id": "resource-1"}
        )


if __name__ == "__main__":
    unittest.main()
