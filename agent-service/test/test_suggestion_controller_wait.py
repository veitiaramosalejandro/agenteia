import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.controllers.suggestions import _wait_for_worker_result


class SuggestionControllerWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_worker_and_returns_completed_contract(self):
        context = {"quoted_chat_id": ""}
        completed = {
            "status": "completed",
            "result": {
                "questionChatId": "1885999530",
                "language": "es",
                "title": None,
                "suggestions": [
                    {
                        "id": "1",
                        "text": "No pude verificar el dato solicitado.",
                    }
                ],
            },
        }
        with patch(
            "app.api.controllers.suggestions._load_response_status",
            side_effect=[{"status": "queued"}, {"status": "thinking"}, completed],
        ), patch("app.api.controllers.suggestions.asyncio.sleep", return_value=None):
            response = await _wait_for_worker_result("1885999530", context)

        self.assertEqual("completed", response.status)
        self.assertEqual(5, response.code)
        self.assertEqual("es", response.language)
        self.assertEqual(1, len(response.suggestions))
        self.assertEqual("1885999530", response.questionChatId)

    async def test_propagates_worker_failure_instead_of_empty_suggestions(self):
        with patch(
            "app.api.controllers.suggestions._load_response_status",
            return_value={"status": "failed", "error": "modelo indisponível"},
        ):
            with self.assertRaises(HTTPException) as raised:
                await _wait_for_worker_result("1885999531", {"quoted_chat_id": ""})

        self.assertEqual(503, raised.exception.status_code)
        self.assertIn("modelo indisponível", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
