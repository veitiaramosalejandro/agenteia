import unittest

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
        self.assertEqual(normalized["IDWorkRoom"], "room-1")
        self.assertEqual(normalized["RawMessage"], "¿Puedes ayudarme?")


if __name__ == "__main__":
    unittest.main()
