import unittest
from unittest.mock import patch
from uuid import uuid4

from app.main import (
    MultiAgentDialogueRequest,
    _create_response_status,
    _framework_message_chat_id,
    _inflate_solidset_form_payload,
    _load_response_status,
    _localize_response_status,
    _update_response_status,
    _route_candidates_to_selected_agents,
    handle_multi_agent_dialogue,
    notification_listener,
)


class TestMultiAgentRouting(unittest.IsolatedAsyncioTestCase):
    def test_framework_request_id_uses_chat_id(self):
        self.assertEqual(
            _framework_message_chat_id({"Chat": {"idChat2": 1824911}}, []),
            "1824911",
        )

    @patch("app.main._dialogue_redis")
    def test_response_status_tracks_agent_stages(self, redis_mock):
        storage = {}
        redis_mock.setex.side_effect = lambda key, ttl, value: storage.__setitem__(key, value)
        redis_mock.get.side_effect = lambda key: storage.get(key)

        created = _create_response_status("request-1", "1823877", 1)
        self.assertEqual(created["status"], "queued")
        _update_response_status(
            "request-1",
            "searching",
            agent_resource_id="agent-1",
            agent_name="Dev17 [IA]",
        )
        _update_response_status("request-1", "completed", response_count=1)

        current = _load_response_status("request-1")
        self.assertEqual(current["status"], "completed")
        self.assertEqual(current["displayMessage"], "Respondido")
        self.assertEqual(current["responseCount"], 1)
        self.assertEqual(current["agents"][0]["status"], "searching")
        self.assertTrue(current["completed"])

        english = _localize_response_status(current, "en")
        portuguese = _localize_response_status(current, "pt")
        self.assertEqual(english["displayMessage"], "Answered")
        self.assertEqual(portuguese["displayMessage"], "Respondido")
        self.assertEqual(
            english["displayMessages"],
            {"es": "Respondido", "en": "Answered", "pt": "Respondido"},
        )

    def test_inflates_preview_form_as_nested_solidset_payload(self):
        payload = _inflate_solidset_form_payload({
            "Sender.Resource": "agent-resource",
            "Info[generated_by_ia]": "1",
            "ExtraData": '{"meeting_id":"meeting-1"}',
            "Chat.Destiny[0].IDResource": "agent-resource",
            "Chat.Destiny[0].TalkWithAgent": "true",
            "Chat.Destiny[1].IDResource": "human-resource",
            "Chat.Destiny[1].Type": 2,
        })

        self.assertEqual(payload["Sender"]["Resource"], "agent-resource")
        self.assertEqual(payload["ExtraData"]["meeting_id"], "meeting-1")
        self.assertTrue(payload["Chat"]["Destiny"][0]["TalkWithAgent"])
        self.assertEqual(
            payload["Chat"]["Destiny"][1]["IDResource"], "human-resource"
        )

    def setUp(self):
        self.mapping_verifier = patch(
            "app.main.verify_and_sync_solidset_agent_mapping",
            side_effect=lambda human_id, expected_id=None: {
                "verified": True,
                "matchesExpected": True,
                "IDHumanResource": human_id,
                "IDAgentResource": expected_id or human_id,
                "changed": False,
            },
        )
        self.verify_mapping = self.mapping_verifier.start()
        self.addCleanup(self.mapping_verifier.stop)

    def test_inactive_sql_agent_mapping_blocks_response(self):
        room_id = uuid4()
        human_agent = uuid4()
        cached_agent = uuid4()
        self.verify_mapping.side_effect = None
        self.verify_mapping.return_value = {
            "verified": False,
            "matchesExpected": False,
            "IDHumanResource": human_agent,
            "IDAgentResource": None,
            "changed": True,
        }
        candidate = {
            "fingerprint": "inactive-sql-agent",
            "channel_id": str(room_id),
            "payload": {
                "Chat": {
                    "destiny": [{
                        "idResource": str(human_agent),
                        "type": 2,
                        "talkWithAgent": True,
                    }],
                },
            },
        }
        configured = [{
            "IDResource": human_agent,
            "IDAgentResource": cached_agent,
            "Name": "Agente obsoleto",
        }]
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments", return_value=0),
            patch("app.main.get_active_agents_for_workroom", return_value=configured),
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        self.assertEqual([], routed)
        self.verify_mapping.assert_called_once_with(str(human_agent))

    def test_talk_with_agent_selects_only_explicit_ai_destination(self):
        room_id = uuid4()
        sender_resource = uuid4()
        sender_login = uuid4()
        selected_agent = uuid4()
        unselected_agent = uuid4()
        candidate = {
            "fingerprint": "talk-with-agent",
            "channel_id": str(room_id),
            "sender_resource": str(sender_resource),
            "payload": {
                "FrameworkDestiny": {
                    "dests": [{"resource": str(unselected_agent), "kind": 2}],
                },
                "Chat": {
                    "resourceTable": [{
                        "idResource": str(sender_resource),
                        "userName": "Alejandro Veitia",
                    }],
                    "destiny": [
                        {
                            "iDLogin": str(sender_login),
                            "iDResource": str(sender_resource),
                            "type": 1,
                            "sequence": 0,
                        },
                        {
                            "iDResource": str(selected_agent),
                            "type": 2,
                            "talkWithAgent": True,
                            "sequence": 1,
                        },
                        {
                            "iDResource": str(unselected_agent),
                            "type": 2,
                            "talkWithAgent": False,
                            "sequence": 2,
                        },
                    ],
                },
            },
        }
        configured = [{
            "IDResource": selected_agent,
            "IDAgentResource": uuid4(),
            "FullName": "Victor Vargas",
        }]
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments", return_value=0) as assign,
            patch("app.main.get_active_agents_for_workroom", return_value=configured) as registry,
            patch("app.main.get_agent_knowledge", return_value=""),
            patch("app.main.get_agent_reinforcement_context", return_value=""),
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        assign.assert_called_once_with(str(room_id), [str(selected_agent)])
        registry.assert_called_once_with(str(room_id), [str(selected_agent)])
        self.assertEqual([str(selected_agent)], [item["agent_resource_id"] for item in routed])
        self.assertEqual(str(sender_resource), routed[0]["reply_resource"])
        self.assertEqual(str(sender_login), routed[0]["reply_login"])
        self.assertEqual("Alejandro Veitia", routed[0]["reply_resource_name"])
        self.assertTrue(routed[0]["reply_destiny_inverted"])

    def test_talk_with_agent_false_prevents_legacy_fallback(self):
        room_id = uuid4()
        agent = uuid4()
        candidate = {
            "fingerprint": "talk-with-agent-false",
            "channel_id": str(room_id),
            "payload": {
                "FrameworkDestiny": {"dests": [{"resource": str(agent), "kind": 2}]},
                "Chat": {"destiny": [{
                    "iDResource": str(agent),
                    "type": 2,
                    "talkWithAgent": False,
                }]},
            },
        }
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments") as assign,
            patch("app.main.get_active_agents_for_workroom") as registry,
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        self.assertEqual([], routed)
        assign.assert_not_called()
        registry.assert_not_called()

    def test_channel_id_falls_back_to_chat_channels(self):
        room_id = uuid4()
        normalized = notification_listener._normalize_framework_message({
            "RawMessage": "Hola agente",
            "Sender": {"resource": str(uuid4())},
            "Destiny": {},
            "Chat": {"channels": [{"idChannel": str(room_id)}]},
        })
        self.assertEqual(str(room_id), normalized["IDWorkRoom"])

    def test_chat_participants_do_not_become_agent_destinations(self):
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
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments") as assign,
            patch("app.main.get_active_agents_for_workroom") as registry,
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        registry.assert_not_called()
        assign.assert_not_called()
        self.assertEqual([], routed)

    def test_routes_only_agent_in_framework_destiny_dests(self):
        room_id = uuid4()
        requested_agent = uuid4()
        other_participant = uuid4()
        sender_resource = uuid4()
        sender_login = uuid4()
        candidate = {
            "fingerprint": "directed-message",
            "channel_id": str(room_id),
            "sender_resource": str(sender_resource),
            "sender_login": str(sender_login),
            "is_direct": False,
            "payload": {
                "FrameworkDestiny": {
                    "workRoom": str(room_id),
                    "dests": [{
                        "login": str(uuid4()),
                        "resource": str(requested_agent),
                        "kind": 2,
                        "sequence": 1,
                    }],
                },
                "SelectedAgentResourceIds": [str(other_participant)],
                "Chat": {"resourceTable": [{"idResource": str(other_participant)}]},
            },
        }
        configured = [{
            "IDResource": requested_agent,
            "IDAgentResource": uuid4(),
            "Name": "Dev20",
            "FullName": "Victor Vargas",
        }]
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments", return_value=0) as assign,
            patch("app.main.get_active_agents_for_workroom", return_value=configured) as registry,
            patch("app.main.get_agent_knowledge", return_value=""),
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        assign.assert_called_once_with(str(room_id), [str(requested_agent)])
        registry.assert_called_once_with(str(room_id), [str(requested_agent)])
        self.assertEqual(1, len(routed))
        self.assertEqual(str(requested_agent), routed[0]["agent_resource_id"])
        self.assertEqual("Asistente IA Victor Vargas", routed[0]["agent_name"])
        self.assertTrue(routed[0]["is_direct"])
        self.assertEqual(str(sender_resource), routed[0]["reply_resource"])
        self.assertEqual(str(sender_login), routed[0]["reply_login"])

    def test_meeting_copy_does_not_activate_senders_own_agent(self):
        room_id = uuid4()
        sender_agent = uuid4()
        candidate = {
            "fingerprint": "meeting-copy-for-author",
            "channel_id": str(room_id),
            "sender_resource": str(sender_agent),
            "meeting_id": str(uuid4()),
            "payload": {
                "FrameworkSender": {"resource": str(sender_agent)},
                "FrameworkDestiny": {
                    "dests": [{"resource": str(sender_agent), "kind": 2, "sequence": 1}],
                },
                "Chat": {
                    "destiny": [{
                        "idResource": str(sender_agent),
                        "type": 1,
                        "sequence": 0,
                    }],
                },
            },
        }
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments") as assign,
            patch("app.main.get_active_agents_for_workroom") as registry,
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        self.assertEqual([], routed)
        assign.assert_not_called()
        registry.assert_not_called()

    def test_meeting_copy_activates_agent_different_from_sender(self):
        room_id = uuid4()
        sender_agent = uuid4()
        requested_agent = uuid4()
        candidate = {
            "fingerprint": "meeting-copy-for-destination",
            "channel_id": str(room_id),
            "sender_resource": str(sender_agent),
            "meeting_id": str(uuid4()),
            "payload": {
                "FrameworkSender": {"resource": str(sender_agent)},
                "FrameworkDestiny": {
                    "dests": [
                        {"resource": str(requested_agent), "kind": 2, "sequence": 1},
                        {"resource": str(sender_agent), "kind": 2},
                        {"resource": str(uuid4()), "kind": 2},
                    ],
                },
                "Chat": {
                    "destiny": [
                        {
                            "idResource": str(sender_agent),
                            "type": 1,
                            "sequence": 0,
                        },
                        {
                            "idResource": str(requested_agent),
                            "type": 2,
                            "sequence": 1,
                        },
                    ],
                },
            },
        }
        configured = [{
            "IDResource": requested_agent,
            "IDAgentResource": uuid4(),
            "Name": "Dev20",
            "FullName": "Victor Vargas",
        }]
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments", return_value=0),
            patch("app.main.get_active_agents_for_workroom", return_value=configured),
            patch("app.main.get_agent_knowledge", return_value=""),
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        self.assertEqual(1, len(routed))
        self.assertEqual(str(requested_agent), routed[0]["agent_resource_id"])
        self.assertEqual("Asistente IA Victor Vargas", routed[0]["agent_name"])

    def test_private_self_chat_can_activate_owners_own_agent(self):
        room_id = uuid4()
        owner_resource = uuid4()
        agent_identity = uuid4()
        candidate = {
            "fingerprint": "private-self-chat",
            "channel_id": str(room_id),
            "sender_resource": str(owner_resource),
            "payload": {
                "FrameworkSender": {"resource": str(owner_resource)},
                "FrameworkDestiny": {"workRoom": str(room_id), "dests": []},
                "Chat": {
                    "channels": [{
                        "idChannel": str(room_id),
                        "channelKind": 1,
                        "kind": 1,
                    }],
                    "destiny": [{
                        "idResource": str(owner_resource),
                        "type": 1,
                        "sequence": 0,
                    }],
                },
            },
        }
        configured = [{
            "ID": agent_identity,
            "IDResource": owner_resource,
            "IDAgentResource": agent_identity,
            "Name": "Dev17",
            "FullName": "Alejandro Veitia",
        }]
        with (
            patch("app.main.ensure_payload_agent_workroom_assignments", return_value=0),
            patch("app.main.get_active_agents_for_workroom", return_value=configured),
            patch("app.main.get_agent_knowledge", return_value=""),
            patch("app.main.get_agent_reinforcement_context", return_value=""),
        ):
            routed = _route_candidates_to_selected_agents([candidate])

        self.assertEqual(1, len(routed))
        self.assertEqual(str(owner_resource), routed[0]["agent_resource_id"])
        self.assertEqual(str(owner_resource), routed[0]["agent_identity_id"])
        self.assertEqual("Asistente IA Alejandro Veitia", routed[0]["agent_name"])

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
            {"IDResource": first, "IDAgentResource": uuid4(), "Name": "Agente A"},
            {"IDResource": second, "IDAgentResource": uuid4(), "Name": "Agente B"},
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
            {"IDResource": first, "IDAgentResource": uuid4(), "Name": "Agente A"},
            {"IDResource": second, "IDAgentResource": uuid4(), "Name": "Agente B"},
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
            patch("app.main.get_agent_reinforcement_context", return_value=""),
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
