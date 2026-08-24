import unittest

from app.main import (
    app,
    _attach_solidset_instance,
    _chat_question_suggestion_context,
    _format_suggestion_scope_context,
    _chat_question_session_id,
    _suggestion_count,
    _local_temporal_response,
    _parse_chat_question_suggestions,
)
from app.agent.orchestrator import SolidSETOrchestrator


class TestChatQuestionSuggestion(unittest.TestCase):
    def test_swagger_contains_fictitious_quoted_message_example(self):
        operation = app.openapi()["paths"][
            "/api/v1/agent/notification/chat-question/suggest-response"
        ]["post"]
        examples = operation["requestBody"]["content"]["application/json"]["examples"]
        payload = examples["quotedMeetingMessage"]["value"]

        self.assertEqual("", payload["Chat"]["rawMessage"])
        self.assertEqual(990002, payload["Chat"]["idChat2"])
        self.assertEqual(990001, payload["Chat"]["chatQuestion"]["idChat2"])
        self.assertTrue(payload["Chat"]["chatQuestion"]["rawMessage"])
        self.assertNotEqual(
            payload["Chat"]["idSenderResource"],
            payload["Chat"]["chatQuestion"]["idSenderResource"],
        )

    def test_swagger_contains_empty_channel_context_example(self):
        operation = app.openapi()["paths"][
            "/api/v1/agent/notification/chat-question/suggest-response"
        ]["post"]
        examples = operation["requestBody"]["content"]["application/json"]["examples"]
        payload = examples["emptyContextAdvice"]["value"]

        # FastAPI omits null fields while encoding OpenAPI examples; omission is
        # equivalent to Chat=null for this optional FrameworkMessage field.
        self.assertIsNone(payload.get("Chat"))
        self.assertEqual("", payload["RawMessage"])
        self.assertEqual("1", payload["Info"]["advice_mode"])
        self.assertTrue(payload["Info"]["request_id"])

    def test_empty_advice_payload_uses_info_and_workroom_context(self):
        payload = {
            "Sender": {
                "session": "00000000-0000-0000-0000-000000000000",
                "login": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "resource": "11111111-1111-4111-8111-111111111111",
                "workRoom": "33333333-3333-4333-8333-333333333333",
            },
            "Destiny": {"workRoom": "33333333-3333-4333-8333-333333333333"},
            "RawMessage": "",
            "Chat": None,
            "Info": {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "advice_mode": "1",
                "request_id": "66666666-6666-4666-8666-666666666666",
            },
        }

        context = _chat_question_suggestion_context(payload)

        self.assertEqual("66666666-6666-4666-8666-666666666666", context["request_id"])
        self.assertEqual("11111111-1111-4111-8111-111111111111", context["session_id"])
        self.assertEqual("33333333-3333-4333-8333-333333333333", context["workroom_id"])
        self.assertEqual("1", context["advice_mode"])
        rendered = _format_suggestion_scope_context(
            [{"message": "Tema pendiente", "sender_full_name": "Ana", "timestamp": None}]
        )
        self.assertEqual("Ana: Tema pendiente", rendered)

    def test_advice_session_is_stable_between_initial_and_continuous_payloads(self):
        common = {
            "requester_resource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
            "workroom_id": "d8e82821-d52f-44bf-9b70-682651a6196e",
        }
        initial = {**common, "request_id": "1757618085", "quoted_chat_id": ""}
        continuous = {**common, "request_id": "1757618087", "quoted_chat_id": "1757618088"}

        self.assertEqual(
            _chat_question_session_id(initial),
            _chat_question_session_id(continuous),
        )

    def test_suggestions_progressively_narrow(self):
        self.assertEqual(3, _suggestion_count(initial=True, completed_turns=0))
        self.assertEqual(2, _suggestion_count(initial=False, completed_turns=1))
        self.assertEqual(1, _suggestion_count(initial=False, completed_turns=2))
        self.assertEqual(1, _suggestion_count(initial=False, completed_turns=10))

    def test_swagger_contains_framework_message_examples_for_related_endpoints(self):
        schema = app.openapi()
        endpoints = (
            "/api/v1/agent/notification/framework-message",
            "/api/v1/agent/notification/framework-message/preview",
            "/api/v1/agent/dialogue",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                examples = schema["paths"][endpoint]["post"]["requestBody"]["content"][
                    "application/json"
                ]["examples"]
                payload = examples["meetingAgentQuestion"]["value"]
                self.assertEqual(990100, payload["Chat"]["idChat2"])
                self.assertTrue(payload["Chat"]["rawMessage"])
                selected = [
                    item
                    for item in payload["Chat"]["destiny"]
                    if item.get("type") == 2 and item.get("talkWithAgent") is True
                ]
                self.assertEqual(1, len(selected))

    def test_parses_distinct_json_suggestions(self):
        result = _parse_chat_question_suggestions(
            '["Resposta direta.", "Resposta breve.", "Resposta colaborativa."]'
        )

        self.assertEqual(
            ["Resposta direta.", "Resposta breve.", "Resposta colaborativa."],
            result,
        )

    def test_parses_fenced_object_and_removes_duplicates(self):
        result = _parse_chat_question_suggestions(
            '```json\n{"suggestions":[{"text":"Sim."},{"text":"Sim."},{"text":"Claro."}]}\n```'
        )

        self.assertEqual(["Sim.", "Claro."], result)

    def test_rejects_model_length_error_as_suggestion(self):
        result = _parse_chat_question_suggestions(
            "⚠️ La consulta es demasiado larga. Por favor, reduce tu mensaje."
        )

        self.assertEqual([], result)

    def test_channel_context_stays_below_prompt_budget_and_keeps_newest(self):
        rows = [
            {
                "message": f"mensaje reciente {index} " + ("x" * 400),
                "sender_full_name": "Ana",
                "timestamp": None,
            }
            for index in range(30)
        ]

        rendered = _format_suggestion_scope_context(rows)

        self.assertLessEqual(len(rendered), 3200)
        self.assertIn("mensaje reciente 0", rendered)

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

    def test_english_time_keeps_english_with_portugal_locale(self):
        response = _local_temporal_response(
            "What time is it?",
            time_zone="Europe/Lisbon",
            locale="pt-PT",
            country_code="PT",
        )

        self.assertIsNotNone(response)
        self.assertTrue(response.startswith("The local time is"))
        self.assertIn("Portugal", response)
        self.assertNotIn("A hora local", response)

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
