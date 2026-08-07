import unittest
from unittest.mock import patch

import httpx

from app.agent.tools import solidset_send_chat_message


class SolidsetSendChatMessageTests(unittest.TestCase):
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
    @patch("app.agent.tools._solidset_request_authenticated")
    def test_sends_directed_channel_message_using_destiny_dests(self, request_mock):
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
        self.assertEqual(form["VisibilityLevel"], 3)
        self.assertEqual(form["Importance"], 3)
        self.assertEqual(form["Info[meeting_mirror_general]"], "1")
        self.assertEqual(form["Info[meeting_id]"], "meeting-123")
        self.assertEqual(form["Info[meeting_code]"], "M8")
        self.assertEqual(form["Kind"], 7)
        self.assertNotIn("Destiny.Resource", form)


if __name__ == "__main__":
    unittest.main()
