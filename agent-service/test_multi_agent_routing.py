import unittest
from unittest.mock import patch
from uuid import uuid4

from app.main import (
    MultiAgentDialogueRequest,
    _route_candidates_to_selected_agents,
    handle_multi_agent_dialogue,
)


class TestMultiAgentRouting(unittest.IsolatedAsyncioTestCase):
    def test_routes_only_active_selected_agents_returned_by_registry(self):
        room_id = uuid4()
        first = uuid4()
        second = uuid4()
        candidate = {
            "fingerprint": "message-1",
            "channel_id": str(room_id),
            "sender_resource": str(uuid4()),
            "payload": {"SelectedAgentResourceIds": [str(first), str(second)]},
        }
        configured = [
            {"IDResource": first, "Name": "Agente A"},
            {"IDResource": second, "Name": "Agente B"},
        ]
        with (
            patch("app.main.get_active_agents_for_workroom", return_value=configured),
            patch("app.main.get_agent_knowledge", return_value="Conocimiento privado"),
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        self.assertEqual(2, len(routed))
        self.assertNotEqual(routed[0]["fingerprint"], routed[1]["fingerprint"])
        self.assertEqual({str(first), str(second)}, {item["agent_resource_id"] for item in routed})

    async def test_dialogue_executes_each_agent_with_isolated_session(self):
        room_id = uuid4()
        first = uuid4()
        second = uuid4()
        conversation_id = uuid4()
        configured = [
            {"IDResource": first, "Name": "Agente A"},
            {"IDResource": second, "Name": "Agente B"},
        ]
        sessions = []

        def invoke(**kwargs):
            sessions.append(kwargs["session_id"])
            return f"Respuesta de {kwargs['message_metadata']['agent_name']}"

        request = MultiAgentDialogueRequest(
            IDWorkRoom=room_id,
            IDSession=conversation_id,
            RawMessage="Diagnostica la alarma",
            SelectedAgentResourceIds=[first, second],
        )
        with (
            patch("app.main.get_active_agents_for_workroom", return_value=configured),
            patch("app.main.get_agent_knowledge", return_value="Conocimiento privado"),
            patch("app.main.touch_agent_session"),
            patch("app.main.orchestrator.invoke", side_effect=invoke),
            patch("app.main._learn_agent_interaction"),
        ):
            result = await handle_multi_agent_dialogue(request)

        self.assertEqual(2, len(result.responses))
        self.assertEqual(2, len(set(sessions)))
        self.assertTrue(all(f"conversation:{conversation_id}" in value for value in sessions))


if __name__ == "__main__":
    unittest.main()
