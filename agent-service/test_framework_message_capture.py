import unittest
import threading
from unittest.mock import patch

from app.system.notification_listener import NotificationApiListener


class TestFrameworkMessageCapture(unittest.TestCase):
    def test_normalizes_framework_message_dto(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        payload = {
            "Stamp": "2026-08-05T12:00:00Z",
            "Sender": {"IDResource": "resource-1", "FullName": "Ana"},
            "Destiny": {"IDWorkRoom": "room-1"},
            "Kind": "ChatMessage",
            "RawMessage": "Mensaje de prueba",
            "Args": ["chat-1", 10, 0],
            "WorkRoomData": {"IDWorkRoom": "room-1", "Name": "Soporte"},
        }

        normalized = listener._normalize_framework_message(payload)

        self.assertEqual(normalized["IDSenderResource"], "resource-1")
        self.assertEqual(normalized["SenderFullName"], "Ana")
        self.assertEqual(normalized["IDWorkRoom"], "room-1")
        self.assertEqual(normalized["ChannelName"], "Soporte")
        self.assertEqual(normalized["IDChat"], "chat-1")
        self.assertEqual(normalized["FrameworkKind"], "ChatMessage")
        self.assertEqual(normalized["RawMessage"], "Mensaje de prueba")

    def test_normalizes_camel_case_notification_payload(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        payload = {
            "stamp": "2026-08-05T16:37:18.2981458Z",
            "sender": {"resource": "resource-1", "login": "login-1"},
            "destiny": {"workRoom": "room-1", "resource": "agent-resource"},
            "kind": "ChatMessage",
            "rawMessage": "¿Puedes ayudarme?",
            "importance": 1,
        }

        normalized = listener._normalize_framework_message(payload)

        self.assertEqual(normalized["Stamp"], payload["stamp"])
        self.assertEqual(normalized["IDSenderResource"], "resource-1")
        self.assertEqual(normalized["IDSenderLogin"], "login-1")
        self.assertEqual(normalized["IDWorkRoom"], "room-1")
        self.assertEqual(normalized["RawMessage"], "¿Puedes ayudarme?")

    def test_detects_agent_in_destiny_dests_even_with_workroom(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        listener.current_login_id = ""
        listener.current_resource_id = ""
        destiny = {
            "workRoom": "room-1",
            "dests": [
                {"login": "agent-login", "resource": "agent-resource", "kind": 2}
            ],
        }
        with (
            patch("app.system.notification_listener.settings.SOLIDSET_LOGIN_RESOURCE_ID", "agent-login"),
            patch("app.system.notification_listener.settings.SOLIDSET_RESOURCE_ID", "agent-resource"),
        ):
            self.assertTrue(listener._destiny_addresses_agent(destiny))

    def test_does_not_address_agent_when_destiny_belongs_to_another_user(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        listener.current_login_id = ""
        listener.current_resource_id = ""
        destiny = {"dests": [{"login": "other-login", "resource": "other-resource"}]}
        with (
            patch("app.system.notification_listener.settings.SOLIDSET_LOGIN_RESOURCE_ID", "agent-login"),
            patch("app.system.notification_listener.settings.SOLIDSET_RESOURCE_ID", "agent-resource"),
        ):
            self.assertFalse(listener._destiny_addresses_agent(destiny))

    def test_builds_direct_reply_to_sender_when_agent_is_in_destinations(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        listener.current_login_id = "agent-login"
        listener.current_resource_id = "agent-resource"
        listener.metrics_lock = threading.Lock()
        listener.recent_auto_reply_candidates = []
        listener.max_recent_auto_reply_candidates = 10
        entry = {
            "source": "framework",
            "endpoint": "/FrameworkHub/SendMessage",
            "channel_id": "room-1",
            "data": {
                "RawMessage": "Preciso do relatório, por favor.",
                "IDWorkRoom": "room-1",
                "IDSenderResource": "sender-resource",
                "FrameworkDestiny": {
                    "workRoom": "room-1",
                    "dests": [{"login": "agent-login", "resource": "agent-resource"}],
                },
            },
        }

        candidate = listener._build_auto_reply_candidate(entry, "fingerprint-1")

        self.assertTrue(candidate["addressed_to_agent"])
        self.assertTrue(candidate["is_direct"])
        self.assertEqual(candidate["reply_resource"], "sender-resource")
        self.assertEqual(candidate["scope"], "directo")


if __name__ == "__main__":
    unittest.main()
