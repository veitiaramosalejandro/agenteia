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

    def close(self):
        self.closed = True


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
        self.assertTrue(FakeOpenAI.last_instance.closed)

    @patch("app.services.external_search.get_llm_provider_configuration")
    def test_agent_connection_is_used_without_inheriting_global_project(self, resolve):
        resolve.return_value = {"APIKey": "agent-key", "Model": "gpt-4.1-mini"}
        with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=FakeOpenAI)}), patch(
            "app.services.external_search.settings.OPENAI_PROJECT", "another-project"
        ):
            search_with_openai("consulta pública", resource_id="agent-a")
        resolve.assert_called_once_with("agent-a", "external_web", provider="openai")
        client = FakeOpenAI.last_instance
        self.assertEqual(client.client_kwargs["api_key"], "agent-key")
        self.assertNotIn("project", client.client_kwargs)
        self.assertEqual(client.responses.kwargs["input"], "consulta pública")

    @patch("app.services.external_search.get_llm_provider_configuration")
    def test_validation_precedes_credentials_and_network(self, resolve):
        for query in [" ", "x" * 2001]:
            with self.assertRaises(ValueError):
                search_with_openai(query, resource_id="agent-a")
        resolve.assert_not_called()

    def test_incomplete_or_unsourced_response_is_rejected_and_client_closed(self):
        for response in [
            SimpleNamespace(status="incomplete", output_text="partial", output=[]),
            SimpleNamespace(status="completed", output_text="uncited", output=[]),
        ]:
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=FakeOpenAI)}), patch(
                "app.services.external_search.settings.OPENAI_API_KEY", "test-key"
            ), patch.object(FakeResponses, "create", return_value=response):
                with self.assertRaises(RuntimeError):
                    search_with_openai("consulta pública")
                self.assertTrue(FakeOpenAI.last_instance.closed)


if __name__ == "__main__":
    unittest.main()
