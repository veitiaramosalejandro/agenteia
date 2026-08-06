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


if __name__ == "__main__":
    unittest.main()
