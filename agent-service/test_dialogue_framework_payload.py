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
        self.assertEqual(dialogue.session_id, "session-1")
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
        self.assertEqual(dialogue.session_id, "room-2")
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


if __name__ == "__main__":
    unittest.main()
