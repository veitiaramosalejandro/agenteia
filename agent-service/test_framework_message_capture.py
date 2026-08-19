import unittest
import threading
from unittest.mock import Mock, patch

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
                "FrameworkKind": "ChatMessage",
                "VisibilityLevel": "Confidential",
                "Info": {
                    "meeting_id": "meeting-123",
                    "meeting_code": "M8",
                },
                "IDWorkRoom": "room-1",
                "IDSenderResource": "sender-resource",
                "IDSenderLogin": "sender-login",
                "Importance": "High",
                "Chat": {
                    "chatQuestionMessage": 1818393,
                    "chatQuestion": {
                        "idChat2": 1818393,
                        "idSenderResource": "quoted-sender-resource",
                        "rawMessage": "Que dia es hoy?",
                        "idMeeting": "old-meeting-id",
                    },
                },
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
        self.assertEqual(candidate["sender_login"], "sender-login")
        self.assertEqual(candidate["recipient_count"], 1)
        self.assertEqual(candidate["importance"], 2)
        self.assertEqual(candidate["scope"], "directo")
        self.assertEqual(candidate["visibility_level"], 2)
        self.assertTrue(candidate["meeting_active"])
        self.assertEqual(candidate["meeting_id"], "meeting-123")
        self.assertEqual(candidate["meeting_code"], "M8")
        self.assertEqual(candidate["quoted_chat_id"], 1818393)
        self.assertEqual(candidate["quoted_message"], "Que dia es hoy?")
        self.assertEqual(
            candidate["quoted_sender_resource"], "quoted-sender-resource"
        )
        self.assertNotEqual(candidate["meeting_id"], "old-meeting-id")
        self.assertTrue(candidate["kind_reply_eligible"])
        self.assertEqual(candidate["message_kind_value"], 7)

    def test_normalizes_all_visibility_level_values(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        self.assertEqual(listener._normalize_visibility_level("Public"), 0)
        self.assertEqual(listener._normalize_visibility_level("Normal"), 1)
        self.assertEqual(listener._normalize_visibility_level("Confidential"), 2)
        self.assertEqual(listener._normalize_visibility_level("Private"), 3)
        self.assertEqual(listener._normalize_visibility_level(0), 0)
        self.assertEqual(listener._normalize_visibility_level("3"), 3)
        self.assertEqual(listener._normalize_visibility_level("invalid"), 1)

    def test_normalizes_all_chat_importance_values(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        self.assertEqual(listener._normalize_chat_importance("Low"), 0)
        self.assertEqual(listener._normalize_chat_importance("Normal"), 1)
        self.assertEqual(listener._normalize_chat_importance("High"), 2)
        self.assertEqual(listener._normalize_chat_importance("Urgent"), 3)
        self.assertEqual(listener._normalize_chat_importance(0), 0)
        self.assertEqual(listener._normalize_chat_importance("3"), 3)
        self.assertEqual(listener._normalize_chat_importance("invalid"), 1)

    def test_meeting_context_uses_meeting_id_without_mirror_flag(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        active = listener._extract_meeting_context({
            "meeting_id": "meeting-1", "meeting_code": "M8"
        })
        from_extra = listener._extract_meeting_context(
            {}, '{"meeting_id":"meeting-2","meeting_code":"M10"}'
        )
        inactive = listener._extract_meeting_context({"meeting_mirror_general": "1"})
        self.assertTrue(active["active"])
        self.assertEqual(active["meeting_id"], "meeting-1")
        self.assertTrue(from_extra["active"])
        self.assertEqual(from_extra["meeting_id"], "meeting-2")
        self.assertEqual(from_extra["meeting_code"], "M10")
        self.assertFalse(inactive["active"])
        self.assertEqual(inactive["meeting_id"], "")

    def test_classifies_chat_kind_and_technical_events(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        self.assertTrue(listener._normalize_message_kind("ChatMessage")["conversational"])
        self.assertTrue(listener._normalize_message_kind(7)["conversational"])
        self.assertTrue(listener._normalize_message_kind("ChatMessageMeetingComment")["conversational"])
        self.assertEqual(listener._normalize_message_kind("ChatMessageMeetingComment")["category"], "meeting")
        self.assertEqual(listener._normalize_message_kind("ChatMessageTaskUpdate")["category"], "task")
        self.assertFalse(listener._normalize_message_kind("ChatMessageRead")["conversational"])
        self.assertFalse(listener._normalize_message_kind("ReactionCodeUpdate")["conversational"])
        self.assertFalse(listener._normalize_message_kind(146)["conversational"])

    def test_duplicate_capture_does_not_create_second_candidate(self):
        listener = NotificationApiListener.__new__(NotificationApiListener)
        listener.current_login_id = "agent-login"
        listener.current_resource_id = "agent-resource"
        listener.metrics_lock = threading.Lock()
        listener.recent_auto_reply_candidates = []
        listener.max_recent_auto_reply_candidates = 10
        listener.recent_captured_messages = []
        listener.max_recent_captured_messages = 10
        listener.seen_fingerprints = []
        listener.max_seen = 100
        listener.sistema = Mock()
        listener.sistema.aprender_actividad.return_value = True
        entry_payload = {
            "RawMessage": "Pode indicar o recurso de Alejandro?",
            "Sender": {"resource": "sender-resource", "login": "sender-login"},
            "Destiny": {
                "workRoom": "room-1",
                "dests": [{"login": "agent-login", "resource": "agent-resource"}],
            },
        }

        first = listener.capture_realtime_payload(entry_payload)
        second = listener.capture_realtime_payload(entry_payload)

        self.assertEqual(len(first["auto_reply_candidates"]), 1)
        self.assertEqual(len(second["auto_reply_candidates"]), 0)
        self.assertEqual(second["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
