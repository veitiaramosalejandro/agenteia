import unittest
from unittest.mock import patch
from uuid import uuid4

from app.api.schemas.common import SolidSETReactionCaptureRequest
from app.api.controllers.feedback import capture_solidset_agent_reaction
from app.system.reaction_capture import (
    classify_reaction,
    reaction_reward,
    resolve_agent_message,
)


class ReactionCaptureTests(unittest.TestCase):
    def test_accepts_solidset_camel_case_reaction_payload(self):
        channel = uuid4()
        user = uuid4()
        request = SolidSETReactionCaptureRequest(
            idChat=1822812,
            idUser=user,
            idChannel=channel,
            idEmoji="U+1F44E",
            counter=1,
        )

        self.assertEqual(1822812, request.IDChat)
        self.assertEqual(user, request.IDUser)
        self.assertEqual(channel, request.IDChannel)
        self.assertEqual("U+1F44E", request.IDEmoji)
        self.assertEqual(1, request.Counter)

    def test_classifies_common_solidset_reactions(self):
        self.assertEqual("positive", classify_reaction("U+1F64F", 1))
        self.assertEqual("positive", classify_reaction("U+1F44D", 1))
        self.assertEqual("negative", classify_reaction("U+1F44E", 1))
        self.assertEqual("neutral", classify_reaction("U+1F914", 1))
        self.assertEqual("removed", classify_reaction("U+1F44D", 0))
        self.assertEqual(1.0, reaction_reward("positive", 1))
        self.assertEqual(-2.0, reaction_reward("negative", 2))

    def test_classifies_frontend_reaction_catalog(self):
        for code in (
            "U+1F44C", "U+1F64F", "U+1F44D", "U+1F499", "U+1F4A1",
            "U+1F44F", "U+1F4AA", "U+2705",
        ):
            with self.subTest(code=code):
                self.assertEqual("positive", classify_reaction(code, 1))
        for code in ("U+1F44E", "U+1F6E0", "U+1F4DD", "U+1F6AB"):
            with self.subTest(code=code):
                self.assertEqual("negative", classify_reaction(code, 1))
        self.assertEqual("neutral", classify_reaction("U+1F50E", 1))

    @patch("app.system.reaction_capture._postgres_connection")
    @patch("app.system.reaction_capture.connect_solidset_sql")
    def test_resolves_agent_by_persisted_identity_without_message_prefix(
        self, connect_sql, postgres_connection
    ):
        chat_cursor = connect_sql.return_value.__enter__.return_value.cursor.return_value
        chat_cursor.fetchone.return_value = {
            "IDChat2": 1766796,
            "RawMessage": "Resposta gerada pelo agente",
            "IDSenderResource": str(uuid4()),
            "IDWorkRoom": uuid4(),
        }
        agent_identity = uuid4()
        sql_resource = chat_cursor.fetchone.return_value["IDSenderResource"]
        chat_cursor.fetchone.return_value["IDSenderResource"] = sql_resource
        pg_cursor = (
            postgres_connection.return_value.__enter__.return_value.cursor.return_value
            .__enter__.return_value
        )
        pg_cursor.fetchone.return_value = {
            "IDResource": uuid4(),
            "IDAgentResource": agent_identity,
            "Name": "Agente",
            "FullName": "Agente Teste",
        }

        result = resolve_agent_message(1766796, {"DataAPI": "configured"})

        self.assertEqual(agent_identity, result["IDAgentResource"])
        self.assertEqual("Resposta gerada pelo agente", result["RawMessage"])
        self.assertIn(sql_resource, [str(v) for v in pg_cursor.execute.call_args.args[1]])

    @patch("app.system.reaction_capture._postgres_connection")
    @patch("app.system.reaction_capture.connect_solidset_sql")
    def test_resolves_when_sender_is_local_agent_identity(
        self, connect_sql, postgres_connection
    ):
        sender_identity = uuid4()
        chat_cursor = connect_sql.return_value.__enter__.return_value.cursor.return_value
        chat_cursor.fetchone.return_value = {
            "IDChat2": 1766796,
            "RawMessage": "texto sin prefijo",
            "IDSenderResource": str(sender_identity),
        }
        pg_cursor = (
            postgres_connection.return_value.__enter__.return_value.cursor.return_value
            .__enter__.return_value
        )
        pg_cursor.fetchone.return_value = {
            "IDResource": uuid4(),
            "IDAgentResource": sender_identity,
            "Name": "Agente",
            "FullName": None,
        }

        result = resolve_agent_message(1766796, {})

        self.assertEqual(sender_identity, result["IDAgentResource"])

    @patch(
        "app.api.controllers.feedback.agent.sistema_aprendizaje.aprender_actividad",
        return_value=True,
    )
    @patch("app.api.controllers.feedback.save_agent_reaction")
    @patch("app.api.controllers.feedback.resolve_agent_message")
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
            "IDResource": agent_resource,
            "IDAgentResource": agent_resource,
            "Name": "Dev20",
            "FullName": "Victor Vargas",
        }
        save_reaction.return_value = ({"ID": uuid4()}, True)

        response = capture_solidset_agent_reaction(
            SolidSETReactionCaptureRequest(
                IDChat=1822812,
                IDUser=user,
                IDChannel=channel,
                IDEmoji="U+1F64F",
                Counter=1,
            )
        )

        self.assertTrue(response.learned)
        self.assertTrue(response.changed)
        self.assertEqual("positive", response.signal)
        self.assertEqual(1.0, response.reward)
        self.assertTrue(response.persisted)
        self.assertEqual(agent_resource, response.IDAgentResource)
        self.assertEqual("Victor Vargas", response.AgentName)
        learn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
