import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.external_search import search_with_openai


class FakeResponses:
    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text="Síntesis sustentada.",
            output=[{
                    "type": "web_search_call",
                    "action": {"sources": [
                        {"title": "Fuente A", "url": "https://example.com/a"},
                        {"title": "Duplicada", "url": "https://example.com/a"},
                    ]},
                }],
        )


class FakeOpenAI:
    last_instance = None

    def __init__(self, **kwargs):
        self.client_kwargs = kwargs
        self.responses = FakeResponses()
        FakeOpenAI.last_instance = self


class TestOpenAIExternalSearch(unittest.TestCase):
    def test_uses_web_search_statelessly_and_returns_cited_result(self):
        fake_module = SimpleNamespace(OpenAI=FakeOpenAI)
        with patch.dict(sys.modules, {"openai": fake_module}), patch(
            "app.services.external_search.settings.OPENAI_API_KEY", "test-key"
        ):
            results = search_with_openai("consulta pública")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://example.com/a")
        request = FakeOpenAI.last_instance.responses.kwargs
        self.assertFalse(request["store"])
        self.assertEqual(request["tools"][0]["type"], "web_search")
        self.assertEqual(request["input"], "consulta pública")


if __name__ == "__main__":
    unittest.main()
