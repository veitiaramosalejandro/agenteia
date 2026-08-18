import unittest
from unittest.mock import patch
from uuid import uuid4

from app.main import SolidSETReactionCaptureRequest, capture_solidset_agent_reaction
from app.system.reaction_capture import classify_reaction


class ReactionCaptureTests(unittest.TestCase):
    def test_classifies_common_solidset_reactions(self):
        self.assertEqual("positive", classify_reaction("U+1F64F", 1))
        self.assertEqual("positive", classify_reaction("U+1F44D", 1))
        self.assertEqual("negative", classify_reaction("U+1F44E", 1))
        self.assertEqual("neutral", classify_reaction("U+1F914", 1))
        self.assertEqual("removed", classify_reaction("U+1F44D", 0))

    @patch("app.main.agent.sistema_aprendizaje.aprender_actividad", return_value=True)
    @patch("app.main.save_agent_reaction")
    @patch("app.main.resolve_agent_message")
    def test_captures_reaction_for_agent_that_emitted_response(
        self, resolve_message, save_reaction, learn
    ):
        agent_resource = uuid4()
        channel = uuid4()
        user = uuid4()
        resolve_message.return_value = {
            "IDChat2": 1822812,
            "RawMessage": "Asistente IA Victor Vargas: respuesta",
            "IDSenderResource": agent_resource,
            "IDWorkRoom": channel,
            "IDAgentResource": agent_resource,
            "Name": "Dev20",
            "FullName": "Victor Vargas",
        }
        save_reaction.return_value = ({"ID": uuid4()}, True)

        response = capture_solidset_agent_reaction(SolidSETReactionCaptureRequest(
            IDChat=1822812,
            IDUser=user,
            IDChannel=channel,
            IDEmoji="U+1F64F",
            Counter=1,
        ))

        self.assertTrue(response.learned)
        self.assertTrue(response.changed)
        self.assertEqual("positive", response.signal)
        self.assertEqual(agent_resource, response.IDAgentResource)
        self.assertEqual("Asistente IA Victor Vargas", response.AgentName)
        learn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
