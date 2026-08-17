import unittest
from unittest.mock import patch
from uuid import uuid4

from app.main import (
    MultiAgentDialogueRequest,
    _route_candidates_to_selected_agents,
    handle_multi_agent_dialogue,
    notification_listener,
)


class TestMultiAgentRouting(unittest.IsolatedAsyncioTestCase):
    def test_channel_id_falls_back_to_chat_channels(self):
        room_id = uuid4()
        normalized = notification_listener._normalize_framework_message({
            "RawMessage": "Hola agente",
            "Sender": {"resource": str(uuid4())},
            "Destiny": {},
            "Chat": {"channels": [{"idChannel": str(room_id)}]},
        })
        self.assertEqual(str(room_id), normalized["IDWorkRoom"])

    def test_routes_active_agent_from_chat_resource_table_even_when_it_is_sender(self):
        room_id = uuid4()
        resource_id = uuid4()
        session_id = uuid4()
        candidate = {
            "fingerprint": "solidset-message",
            "channel_id": str(room_id),
            "sender_resource": str(resource_id),
            "payload": {
                "FrameworkSender": {"session": str(session_id), "resource": str(resource_id)},
                "Chat": {
                    "channels": [{"idChannel": str(room_id)}],
                    "resourceTable": [{"idResource": str(resource_id)}],
                    "destiny": [{"idResource": str(resource_id)}],
                },
            },
        }
        configured = [{"IDResource": resource_id, "Name": "Agente Dev17"}]
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments", return_value=1) as assign,
            patch("app.main.get_active_agents_for_workroom", return_value=configured) as registry,
            patch("app.main.get_agent_knowledge", return_value=""),
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        registry.assert_called_once_with(str(room_id), [str(resource_id)])
        assign.assert_called_once_with(str(room_id), [str(resource_id)])
        self.assertEqual(1, len(routed))
        self.assertEqual(str(resource_id), routed[0]["agent_resource_id"])
        self.assertEqual(str(session_id), routed[0]["agent_session_id"])

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
            patch("app.main.ensure_payload_agent_workroom_assignments", return_value=0),
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
