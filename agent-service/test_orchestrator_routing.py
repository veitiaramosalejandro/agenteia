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

    def test_unseen_general_topic_is_not_internal_work(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertFalse(agent._is_internal_domain_query(
            "¿Qué avances recientes existen en baterías de estado sólido?"
        ))

    def test_cnc_and_solidset_remain_internal_work(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._is_internal_domain_query("¿Qué significa esta alarma CNC?"))
        self.assertTrue(agent._is_internal_domain_query("Resume los mensajes del canal SolidSET"))

    def test_guid_validation_rejects_fallback_identifiers(self):
        self.assertFalse(MachiningAgent._is_valid_guid("framework-user"))
        self.assertFalse(MachiningAgent._is_valid_guid("framework-dialogue"))
        self.assertFalse(MachiningAgent._is_valid_guid(None))
        self.assertTrue(
            MachiningAgent._is_valid_guid("1790fc78-023d-4506-a7e8-5c030e9386d1")
        )


if __name__ == "__main__":
    unittest.main()
