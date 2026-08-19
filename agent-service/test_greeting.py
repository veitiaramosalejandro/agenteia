import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from app.agent.core import MachiningAgent


class GreetingPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.agent = object.__new__(MachiningAgent)
        self.agent.sistema_aprendizaje = Mock()

    def test_greeting_uses_full_name_without_disclosing_other_context(self):
        self.agent.sistema_aprendizaje.obtener_contexto_usuario.return_value = SimpleNamespace(
            usuario=SimpleNamespace(nombre="Alejandro Veitia", rol="Dev17"),
            canales_acceso=[object()] * 161,
        )
        response = self.agent._handle_greeting(
            user_id="ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            user_text="Hola",
        )

        self.agent.sistema_aprendizaje.obtener_contexto_usuario.assert_called_once()
        self.assertEqual(
            "👋 ¡Hola, Alejandro Veitia! Es un placer saludarte. ¿En qué puedo ayudarte?",
            response,
        )
        self.assertNotIn("Dev17", response)
        self.assertNotIn("canal", response.lower())
        self.assertNotIn("161", response)

    def test_greeting_remains_neutral_in_supported_languages(self):
        portuguese = self.agent._handle_greeting(user_text="Olá")
        english = self.agent._handle_greeting(user_text="Hello")

        self.assertNotIn("perfil", portuguese.lower())
        self.assertNotIn("canais", portuguese.lower())
        self.assertNotIn("profile", english.lower())
        self.assertNotIn("channels", english.lower())

    def test_greeting_is_neutral_when_full_name_cannot_be_resolved(self):
        self.agent.sistema_aprendizaje.obtener_contexto_usuario.side_effect = RuntimeError(
            "context unavailable"
        )

        response = self.agent._handle_greeting(
            user_id="ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            user_text="Hola",
        )

        self.assertEqual(
            "👋 ¡Hola! Es un placer saludarte. ¿En qué puedo ayudarte?",
            response,
        )


if __name__ == "__main__":
    unittest.main()
