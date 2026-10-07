import unittest
from unittest.mock import MagicMock, patch

import httpx

from app.agent.tools import (
    _solidset_instance_for_base,
    _solidset_login,
    _solidset_request_as_agent,
    solidset_send_chat_message,
)


class SolidsetSendChatMessageTests(unittest.TestCase):
    @patch("app.agent.tools.list_active_solidset_instances")
    @patch("app.agent.tools.os.path.exists", return_value=True)
    def test_docker_runtime_url_resolves_logical_instance(self, _exists, instances):
        expected = {"ID": "instance-1", "BaseUrl": "http://localhost:52130"}
        instances.return_value = [expected]
        self.assertIs(
            expected,
            _solidset_instance_for_base("http://host.docker.internal:52130"),
        )

    @patch("app.agent.tools._solidset_request_as_agent")
    def test_preview_returns_payload_without_calling_solidset(self, request_mock):
        result = solidset_send_chat_message.invoke(
            {
                "canal_id": "channel-123",
                "mensaje": "Respuesta previa",
                "recurso_id": "human-resource",
                "recurso_login_id": "owner-login",
                "confirm": True,
                "generated_by_ia": True,
                "agent_resource_id": "human-resource",
                "agent_identity_id": "agent-resource",
                "agent_chat_resource_name": "Dev17 [IA]",
                "agent_chat_login_id": "owner-login",
                "human_chat_resource_name": "Alejandro Veitia",
                "preview_only": True,
            }
        )

        request_mock.assert_not_called()
        payload = __import__("json").loads(result)
        self.assertEqual(payload["Sender.Resource"], "agent-resource")
        self.assertEqual(payload["Chat.Destiny[1].IDResource"], "human-resource")

    @patch("app.agent.tools.get_solidset_login_for_active_agent")
    def test_login_rejects_resource_without_active_agent(self, login_lookup):
        login_lookup.return_value = None
        client = MagicMock()

        authenticated, endpoint, access_key = _solidset_login(
            client,
            "http://solidset.local",
            agent_resource_id="ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
        )

        self.assertFalse(authenticated)
        self.assertEqual(endpoint, "")
        self.assertEqual(access_key, "")
        client.post.assert_not_called()

    @patch("app.agent.tools.get_solidset_login_for_active_agent")
    @patch("app.agent.tools.httpx.Client")
    def test_agent_authenticates_through_login_json(
        self, client_class, login_lookup
    ):
        login_lookup.return_value = {
            "IDLogin": "3d2d097f-34c1-4cf7-acd5-067453381511",
            "Username": "agent.user",
            "Password": "secret-value",
        }
        client = MagicMock()
        client_class.return_value.__enter__.return_value = client
        client.post.return_value = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
            json=lambda: {"Success": True},
        )
        client.cookies.items.return_value = []
        expected_response = httpx.Response(200, json={"Result": 0})
        client.request.return_value = expected_response

        response, base, error = _solidset_request_as_agent(
            agent_resource_id="ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            agent_login_id="3d2d097f-34c1-4cf7-acd5-067453381511",
            method="POST",
            endpoint="/Chat/SendMessageForm",
            form_payload={"RawMessage": "Agente Dev17: respuesta"},
            solidset_base_url="http://solidset.local",
        )

        self.assertIs(response, expected_response)
        self.assertEqual(base, "http://solidset.local")
        self.assertEqual(error, "")
        login_call = client.post.call_args
        self.assertEqual(login_call.args[0], "http://solidset.local/User/LoginJson")
        self.assertEqual(login_call.kwargs["data"]["UserName"], "agent.user")
        self.assertEqual(login_call.kwargs["data"]["Password"], "secret-value")
        self.assertEqual(login_call.kwargs["data"]["PasswordEncrypted"], "true")
        self.assertEqual(login_call.kwargs["data"]["TimezoneId"], "GMT Standard Time")
        self.assertNotIn("Resources[0]", login_call.kwargs["data"])
        login_lookup.assert_called_once_with(
            "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            preferred_login_id="3d2d097f-34c1-4cf7-acd5-067453381511",
        )

    @patch("app.system.resource_ingest.ingest_solidset_logins")
    @patch("app.agent.tools.get_solidset_login_for_active_agent")
    def test_rejected_login_resynchronizes_and_retries_once(
        self, login_lookup, sync_logins
    ):
        login_lookup.side_effect = [
            {
                "IDLogin": "old-login",
                "Username": "agent.user",
                "Password": "old-password",
            },
            {
                "IDLogin": "3d2d097f-34c1-4cf7-acd5-067453381511",
                "Username": "agent.user",
                "Password": "new-password",
            },
        ]
        sync_logins.return_value = {"updated": 1, "inserted": 0}
        rejected = MagicMock(status_code=200)
        rejected.json.return_value = {"Success": False}
        accepted = MagicMock(status_code=200, headers={})
        accepted.json.return_value = {"Success": True}
        client = MagicMock()
        client.post.side_effect = [rejected, accepted]

        authenticated, endpoint, _ = _solidset_login(
            client,
            "http://solidset.local",
            agent_resource_id="ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            agent_login_id="3d2d097f-34c1-4cf7-acd5-067453381511",
        )

        self.assertTrue(authenticated)
        self.assertEqual(endpoint, "/User/LoginJson")
        sync_logins.assert_called_once_with()
        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(client.post.call_args.kwargs["data"]["Password"], "new-password")

    @patch("app.agent.tools.settings.SOLIDSET_USER_ACTIONS_ENABLED", True)
    @patch("app.agent.tools._solidset_request_authenticated")
    @patch("app.agent.tools._solidset_request_as_agent")
    def test_agent_response_uses_its_own_solidset_login(
        self, agent_request_mock, global_request_mock
    ):
        agent_request_mock.return_value = (
            httpx.Response(200, json={"Result": 0}),
            "http://solidset.local",
            "",
        )

        result = solidset_send_chat_message.invoke(
            {
                "canal_id": "channel-123",
                "mensaje": "Respuesta del agente",
                "recurso_id": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "recurso_login_id": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "confirm": True,
                "generated_by_ia": True,
                "agent_resource_id": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "agent_identity_id": "2555288c-44c7-4209-95f2-3de98f0f416d",
                "agent_chat_resource_name": "Dev17 [IA]",
                "agent_chat_login_id": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "human_chat_resource_name": "Alejandro Veitia",
                "solidset_base_url": "http://solidset.local",
            }
        )

        self.assertIn("Mensaje enviado", result)
        global_request_mock.assert_not_called()
        self.assertEqual(
            agent_request_mock.call_args.kwargs["agent_resource_id"],
            "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
        )
        self.assertEqual(
            agent_request_mock.call_args.kwargs["agent_login_id"],
            "1790fc78-023d-4506-a7e8-5c030e9386d1",
        )
        self.assertEqual(
            agent_request_mock.call_args.kwargs["solidset_base_url"],
            "http://solidset.local",
        )
        form = agent_request_mock.call_args.kwargs["form_payload"]
        self.assertEqual(
            form["IDAgentIA"], "2555288c-44c7-4209-95f2-3de98f0f416d"
        )
        self.assertEqual(
            form["Info[id_agent_ia]"], "2555288c-44c7-4209-95f2-3de98f0f416d"
        )
        self.assertEqual(
            form["Info[agent_resource_id]"],
            "2555288c-44c7-4209-95f2-3de98f0f416d",
        )
        self.assertNotEqual(
            form["Info[agent_resource_id]"],
            "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
        )
        self.assertEqual(
            form["Chat.IDSenderResource"],
            "2555288c-44c7-4209-95f2-3de98f0f416d",
        )
        self.assertEqual(
            form["Sender.Resource"], "2555288c-44c7-4209-95f2-3de98f0f416d"
        )
        self.assertEqual(
            form["Sender.Login"], "1790fc78-023d-4506-a7e8-5c030e9386d1"
        )
        self.assertEqual(
            form["Sender.Session"], "00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(
            form["Sender.WorkRoom"], "00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(form["Chat.IDWorkRoom"], "channel-123")
        self.assertEqual(form["Chat.RawMessage"], "Respuesta del agente")
        self.assertEqual(form["Chat.Kind"], 60)
        self.assertEqual(
            form["Chat.IDSender"], "1790fc78-023d-4506-a7e8-5c030e9386d1"
        )
        self.assertEqual(
            form["Chat.Destiny[0].IDResource"],
            "2555288c-44c7-4209-95f2-3de98f0f416d",
        )
        self.assertEqual(form["Chat.Destiny[0].ResourceName"], "Dev17 [IA]")
        self.assertEqual(form["Chat.Destiny[0].TalkWithAgent"], "true")
        self.assertEqual(form["Chat.Destiny[0].Type"], 1)
        self.assertEqual(
            form["Chat.Destiny[1].IDResource"],
            "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
        )
        self.assertEqual(form["Chat.Destiny[1].ResourceName"], "Alejandro Veitia")
        self.assertEqual(form["Chat.Destiny[1].Type"], 2)
        self.assertNotIn("Chat.Destiny[1].TalkWithAgent", form)

    @patch("app.agent.tools.settings.SOLIDSET_USER_ACTIONS_ENABLED", True)
    @patch("app.agent.tools._solidset_request_authenticated")
    def test_sends_long_message_in_form_body_not_query_string(self, request_mock):
        request_mock.return_value = (
            httpx.Response(200, json={"ok": True}),
            "http://solidset.local",
            "",
        )
        long_message = "resumo " * 2000

        result = solidset_send_chat_message.invoke(
            {
                "canal_id": "channel-123",
                "mensaje": long_message,
                "confirm": True,
            }
        )

        self.assertIn("Mensaje enviado", result)
        request_mock.assert_called_once()
        call = request_mock.call_args.kwargs
        self.assertEqual(call["method"], "POST")
        self.assertNotIn("params", call)
        self.assertEqual(call["form_payload"]["RawMessage"], long_message.strip())
        self.assertEqual(call["form_payload"]["Destiny.WorkRoom"], "channel-123")

    @patch("app.agent.tools.settings.SOLIDSET_USER_ACTIONS_ENABLED", True)
    @patch("app.agent.tools._resolve_solidset_meeting_id", return_value="meeting-123")
    @patch("app.agent.tools._solidset_request_authenticated")
    def test_sends_directed_channel_message_using_destiny_dests(
        self, request_mock, meeting_mock
    ):
        request_mock.return_value = (
            httpx.Response(200, json={"Result": 0}),
            "http://solidset.local",
            "",
        )

        result = solidset_send_chat_message.invoke(
            {
                "canal_id": "channel-123",
                "mensaje": "Respuesta formal",
                "recurso_id": "sender-resource",
                "recurso_login_id": "sender-login",
                "visibility_level": "Private",
                "importance": "Urgent",
                "meeting_mirror_general": True,
                "meeting_id": "meeting-123",
                "meeting_code": "M8",
                "confirm": True,
            }
        )

        self.assertIn("Mensaje enviado", result)
        form = request_mock.call_args.kwargs["form_payload"]
        self.assertEqual(form["Destiny.WorkRoom"], "channel-123")
        self.assertEqual(form["Destiny.Dests[0].Login"], "sender-login")
        self.assertEqual(form["Destiny.Dests[0].Resource"], "sender-resource")
        self.assertEqual(form["Destiny.Dests[0].Kind"], 2)
        self.assertEqual(form["Destiny.Dests[0].Type"], 2)
        self.assertNotIn("Destiny.Dests[0].TalkWithAgent", form)
        self.assertEqual(form["VisibilityLevel"], 3)
        self.assertEqual(form["Importance"], 3)
        self.assertNotIn("Info[meeting_mirror_general]", form)
        self.assertEqual(form["Info[meeting_id]"], "meeting-123")
        self.assertEqual(form["Info[meeting_code]"], "M8")
        self.assertEqual(
            form["ExtraData"],
            '{"meeting_id":"meeting-123","meeting_code":"M8"}',
        )
        self.assertEqual(form["Kind"], 7)
        self.assertNotIn("Destiny.Resource", form)
        meeting_mock.assert_called_once_with(
            "meeting-123", "channel-123", "M8", None
        )


if __name__ == "__main__":
    unittest.main()
