import unittest
from unittest.mock import MagicMock, patch

import httpx

from app.agent.tools import _solidset_login, _solidset_request_as_agent, solidset_send_chat_message


class SolidsetSendChatMessageTests(unittest.TestCase):
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
        self.assertEqual(
            login_call.kwargs["data"]["Resources[0]"],
            "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
        )

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
                "confirm": True,
                "generated_by_ia": True,
                "agent_resource_id": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "agent_identity_id": "2555288c-44c7-4209-95f2-3de98f0f416d",
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
        meeting_mock.assert_called_once_with("meeting-123", "channel-123", "M8")


if __name__ == "__main__":
    unittest.main()
