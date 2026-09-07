import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from app.agent.core import MachiningAgent


class AgentModelContinuityTests(unittest.TestCase):
    def setUp(self):
        self.agent = object.__new__(MachiningAgent)
        self.agent.llm = Mock()
        self.agent.llm.invoke.side_effect = AssertionError("Global model must not run")

    def test_tool_synthesis_keeps_each_twins_model(self):
        for text in ("Respuesta del gemelo A.", "Respuesta del gemelo B."):
            model = Mock()
            model.invoke.return_value = AIMessage(content=[{"type": "text", "text": text}])
            answer = self.agent._synthesize_tool_response(
                [], "Resume el resultado", request_llm=model
            )
            self.assertEqual(answer, text)
            model.invoke.assert_called_once()
        self.agent.llm.invoke.assert_not_called()

    @patch("app.agent.core.google_web_search")
    def test_web_fallback_preserves_model_and_search_identity(self, search):
        search.invoke.return_value = "Evidencia publica de prueba."
        for resource in ("twin-a", "twin-b"):
            model = Mock()
            model.invoke.return_value = AIMessage(content=[
                {"type": "text", "text": "Respuesta comprobada."}
            ])
            answer = self.agent._answer_with_web_fallback(
                "Pregunta", [], search_query="consulta publica",
                agent_resource_id=resource, request_llm=model,
            )
            self.assertEqual(answer, "Respuesta comprobada.")
            search.invoke.assert_called_with(
                {"query": "consulta publica"},
                config={"configurable": {"agent_resource_id": resource}},
            )
            model.invoke.assert_called_once()
        self.agent.llm.invoke.assert_not_called()

    @patch("app.agent.core.google_web_search")
    def test_provider_failure_does_not_switch_to_global_model(self, search):
        search.invoke.return_value = "Evidencia publica."
        model = Mock()
        model.invoke.side_effect = TimeoutError("test timeout")
        self.assertIsNone(self.agent._synthesize_tool_response(
            [], "Pregunta", request_llm=model
        ))
        self.assertIsNone(self.agent._answer_with_web_fallback(
            "Pregunta", [], search_query="consulta", request_llm=model
        ))
        self.agent.llm.invoke.assert_not_called()

    def test_text_normalization_supports_ollama_and_openai(self):
        for content in ("Respuesta.", [{"type": "text", "text": "Respuesta."}]):
            self.assertEqual(
                self.agent._llm_response_text(AIMessage(content=content)), "Respuesta."
            )

    def test_non_text_blocks_are_not_exposed(self):
        response = AIMessage(content=[
            {"type": "reasoning", "text": "internal"},
            {"type": "text", "text": "Primera."},
            {"type": "text", "text": "Segunda."},
        ])
        self.assertEqual(self.agent._llm_response_text(response), "Primera.\nSegunda.")


if __name__ == "__main__":
    unittest.main()
