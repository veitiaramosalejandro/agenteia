import unittest

from app.agent.core import MachiningAgent


class TestConversationRouting(unittest.TestCase):
    def test_social_messages_are_general_conversation(self):
        messages = [
            "Hola, ¿cómo estás?",
            "hola agente como estas, que nombre te pusieras, quisieras tener algun nombre?",
            "¿Cómo te gustaría que te llamara?",
            "Prefiero llamarte Alex",
            "Te llamaré Alex",
        ]

        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(MachiningAgent._is_general_conversation(message))

    def test_greeting_does_not_hide_a_business_request(self):
        self.assertFalse(
            MachiningAgent._is_general_conversation(
                "Hola, dime los últimos mensajes del canal"
            )
        )

    def test_guid_validation_rejects_fallback_identifiers(self):
        self.assertFalse(MachiningAgent._is_valid_guid("framework-user"))
        self.assertFalse(MachiningAgent._is_valid_guid("framework-dialogue"))
        self.assertFalse(MachiningAgent._is_valid_guid(None))
        self.assertTrue(
            MachiningAgent._is_valid_guid("1790fc78-023d-4506-a7e8-5c030e9386d1")
        )


if __name__ == "__main__":
    unittest.main()
