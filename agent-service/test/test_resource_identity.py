import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.core import MachiningAgent


class TestResourceIdentity(unittest.TestCase):
    def setUp(self):
        self.agent = object.__new__(MachiningAgent)

    def test_detects_resource_identity_questions(self):
        questions = [
            "En este canal cual es mi recurso asociado?",
            "¿Cuál es mi recurso?",
            "¿Qué recurso tengo?",
            "Dime el recurso vinculado a mi sesión",
        ]

        for question in questions:
            with self.subTest(question=question):
                self.assertTrue(self.agent._is_identity_intent(question))

    def test_identity_response_is_formal_and_does_not_expose_internal_ids(self):
        resource_guid = "272700d8-d1ba-46a6-a121-b76fce8ecb9f"
        login_guid = "1790fc78-023d-4506-a7e8-5c030e9386d1"
        channel_guid = "d8e82821-d52f-44bf-9b70-682651a6196e"
        self.agent.sistema_aprendizaje = SimpleNamespace(
            obtener_contexto_usuario=lambda _user_id: None,
        )

        response = self.agent._build_identity_response(
            user_id=resource_guid,
            canal_id=channel_guid,
            authenticated_identity={
                "resource_id": resource_guid,
                "login_id": login_guid,
                "display_name": "Victor Vargas (Dev20)",
            },
        )

        self.assertIn("Victor Vargas", response)
        self.assertIn("Dev20", response)
        self.assertIn("¿En qué puedo ayudarte?", response)
        self.assertNotIn(resource_guid, response)
        self.assertNotIn(login_guid, response)
        self.assertNotIn(channel_guid, response)
        self.assertNotIn("resource_guid", response)

    def test_runtime_model_identity_uses_verified_provider_and_model(self):
        self.assertTrue(self.agent._is_agent_runtime_identity_intent(
            "¿Qué modelo estás usando?"
        ))
        response = self.agent._build_agent_runtime_identity_response({
            "effective_llm_provider": "ollama",
            "effective_llm_model": "qwen2.5:3b",
        })
        self.assertIn("ollama", response)
        self.assertIn("qwen2.5:3b", response)

    def test_runtime_model_identity_fails_closed_when_unverified(self):
        with patch("app.agent.core.get_llm_provider_configuration", return_value=None):
            response = self.agent._build_agent_runtime_identity_response({})
        self.assertEqual(
            "No puedo verificar el proveedor o modelo activo en esta ejecución.",
            response,
        )


if __name__ == "__main__":
    unittest.main()
