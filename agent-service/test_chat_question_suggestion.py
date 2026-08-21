import unittest

from app.main import (
    _attach_solidset_instance,
    _chat_question_suggestion_context,
    _local_temporal_response,
)
from app.agent.orchestrator import SolidSETOrchestrator


class TestChatQuestionSuggestion(unittest.TestCase):
    def test_separates_requester_from_quoted_author(self):
        payload = {
            "Sender": {
                "session": "759f278c-041a-4fd1-b53d-96cc6487d8cc",
                "login": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "resource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            },
            "Chat": {
                "idChat2": 1824967,
                "idSender": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "idSenderResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "idWorkRoom": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                "chatQuestionMessage": 1824966,
                "chatQuestion": {
                    "idChat2": 1824966,
                    "idSender": "2769fb41-1a0c-4ecf-bf54-17deed50d8b4",
                    "idSenderResource": "2dcf6097-a582-4dc3-b4be-d53bb0897461",
                    "rawMessage": "He entendido la propuesta. ¿Confirmas el plazo?",
                    "idMeeting": "7d724ee5-4a56-43cc-a87f-e9f276cbbc01",
                },
            },
            "Info": {"meeting_code": "M11"},
        }

        result = _chat_question_suggestion_context(payload)

        self.assertEqual("1824967", result["request_id"])
        self.assertEqual("1824966", result["quoted_chat_id"])
        self.assertEqual(
            "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            result["requester_resource"],
        )
        self.assertEqual(
            "2dcf6097-a582-4dc3-b4be-d53bb0897461",
            result["quoted_resource"],
        )
        self.assertEqual("He entendido la propuesta. ¿Confirmas el plazo?", result["quoted_message"])
        self.assertEqual("M11", result["meeting_code"])

    def test_suggestion_is_forced_to_private_work_route(self):
        class ExternalTopicAgent:
            @staticmethod
            def _is_general_conversation(_text):
                return False

            @staticmethod
            def _is_external_information_query(_text):
                return True

            @staticmethod
            def _is_internal_domain_query(_text):
                return False

        orchestrator = SolidSETOrchestrator.__new__(SolidSETOrchestrator)
        orchestrator.agent = ExternalTopicAgent()

        result = orchestrator._classify({
            "session_id": "suggestion-test",
            "user_text": "¿Cuál fue el último resultado deportivo?",
            "message_metadata": {"response_suggestion_mode": True},
        })

        self.assertEqual("work_sql_rag", result["route"])

    def test_portugal_date_uses_configured_region(self):
        response = _local_temporal_response(
            "Que dia é hoje?",
            time_zone="Europe/Lisbon",
            locale="pt-PT",
            country_code="PT",
        )

        self.assertIsNotNone(response)
        self.assertIn("Portugal", response)
        self.assertIn("Europe/Lisbon", response)
        self.assertNotIn("Brasília", response)

    def test_payload_region_overrides_instance_default(self):
        candidates = [{"fingerprint": "message-1", "payload": {
            "Info": {
                "country_code": "ES",
                "locale": "es-ES",
                "time_zone": "Europe/Madrid",
            }
        }}]
        _attach_solidset_instance(candidates, {
            "ID": "instance-1",
            "Code": "solidset-pt",
            "BaseUrl": "http://solidset.local",
            "CountryCode": "PT",
            "Locale": "pt-PT",
            "TimeZone": "Europe/Lisbon",
        })

        self.assertEqual("ES", candidates[0]["country_code"])
        self.assertEqual("es-ES", candidates[0]["locale"])
        self.assertEqual("Europe/Madrid", candidates[0]["time_zone"])


if __name__ == "__main__":
    unittest.main()
