import inspect
import uuid
import unittest

from fastapi import HTTPException

from app.main import FrameworkMessageDTO, _framework_message_to_dialogue, handle_dialogue


class TestDialogueFrameworkPayload(unittest.TestCase):
    def test_dialogue_exposes_only_framework_message_contract(self):
        parameter = inspect.signature(handle_dialogue).parameters["message"]

        self.assertIs(parameter.annotation, FrameworkMessageDTO)

    def test_dialogue_rejects_payload_without_raw_message(self):
        with self.assertRaises(HTTPException) as context:
            handle_dialogue(FrameworkMessageDTO(session_id="legacy", message="legacy"))

        self.assertEqual(context.exception.status_code, 422)

    def test_maps_pascal_case_framework_message_to_dialogue(self):
        payload = FrameworkMessageDTO(
            RawMessage="Necesito ayuda con la máquina",
            Sender={
                "login": "user-login",
                "resource": "user-resource",
                "session": "session-1",
            },
            Destiny={"workRoom": "room-1"},
        )

        dialogue = _framework_message_to_dialogue(payload)

        self.assertEqual(dialogue.message, "Necesito ayuda con la máquina")
        self.assertEqual(dialogue.user_id, "user-login")
        self.assertEqual(dialogue.session_id, "user-resource")
        self.assertEqual(dialogue.canal_id, "room-1")

    def test_maps_camel_case_aliases_and_uses_channel_as_session_fallback(self):
        payload = FrameworkMessageDTO.model_validate({
            "rawMessage": "¿Cuál es el estado?",
            "sender": {
                "IDResource": "resource-1",
                "session": "00000000-0000-0000-0000-000000000000",
            },
            "destiny": {"IDWorkRoom": "room-2"},
        })

        dialogue = _framework_message_to_dialogue(payload)

        self.assertEqual(dialogue.user_id, "resource-1")
        self.assertEqual(dialogue.session_id, "resource-1")
        self.assertEqual(dialogue.canal_id, "room-2")

    def test_missing_sender_gets_isolated_session_and_anonymous_user(self):
        first = _framework_message_to_dialogue(FrameworkMessageDTO(RawMessage="Hola"))
        second = _framework_message_to_dialogue(FrameworkMessageDTO(RawMessage="Hola"))

        self.assertTrue(first.session_id.startswith("framework-dialogue-"))
        self.assertTrue(first.user_id.startswith("framework-user-"))
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertNotEqual(first.user_id, second.user_id)
        uuid.UUID(first.session_id.removeprefix("framework-dialogue-"))
        uuid.UUID(first.user_id.removeprefix("framework-user-"))

    def test_maps_direct_resource_conversation_payload(self):
        payload = FrameworkMessageDTO.model_validate({
            "Stamp": "2026-08-12T14:57:40.939190Z",
            "Sender": {
                "room": "00000000-0000-0000-0000-000000000000",
                "session": "00000000-0000-0000-0000-000000000000",
                "login": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "resource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "conversationId": 0,
            },
            "Destiny": {
                "session": "00000000-0000-0000-0000-000000000000",
                "conversationId": 0,
                "workRoom": "45d1a3a4-752b-478b-b9f2-1b0771084172",
                "dests": [],
            },
            "ExternalDestinations": None,
            "Kind": 7,
            "RawMessage": "Hola como estas?",
            "Chat": {
                "idChat2": -2024230439,
                "idSender": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "idSenderResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "rawMessage": "Hola como estas?",
                "idWorkRoom": "45d1a3a4-752b-478b-b9f2-1b0771084172",
            },
        })

        dialogue = _framework_message_to_dialogue(payload)

        self.assertEqual(dialogue.message, "Hola como estas?")
        self.assertEqual(dialogue.user_id, "1790fc78-023d-4506-a7e8-5c030e9386d1")
        self.assertEqual(dialogue.session_id, "ce0e837a-fe28-47ae-9ba0-8841fe042ca8")
        self.assertEqual(dialogue.canal_id, "45d1a3a4-752b-478b-b9f2-1b0771084172")

    def test_uses_chat_fields_as_fallback(self):
        payload = FrameworkMessageDTO(
            Sender={"resource": "resource-2"},
            Chat={
                "idChat2": 1234,
                "rawMessage": "Mensaje desde Chat",
                "idWorkRoom": "room-from-chat",
            },
        )

        dialogue = _framework_message_to_dialogue(payload)

        self.assertEqual(dialogue.message, "Mensaje desde Chat")
        self.assertEqual(dialogue.session_id, "resource-2")
        self.assertEqual(dialogue.canal_id, "room-from-chat")


if __name__ == "__main__":
    unittest.main()
