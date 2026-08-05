import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.tools import google_web_search
from app.agent.core import MachiningAgent


class FakeDDGS:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def text(self, query, **kwargs):
        return [
            {"title": "Manual técnico", "body": "Explicación comprobable", "href": "https://example.com/manual"},
            {"title": "Duplicado", "body": "Otra copia", "href": "https://example.com/manual"},
        ]


class TestWebSearch(unittest.TestCase):
    @patch("app.agent.tools._store_web_search_knowledge", return_value=True)
    def test_search_returns_sources_and_learns(self, store):
        fake_module = SimpleNamespace(DDGS=FakeDDGS)
        with patch.dict(sys.modules, {"ddgs": fake_module}):
            response = google_web_search.invoke({"query": "alarma CNC"})
        payload = json.loads(response)

        self.assertTrue(payload["learned"])
        self.assertTrue(payload["external_unverified"])
        self.assertEqual(payload["results"][0]["url"], "https://example.com/manual")
        self.assertEqual(len(payload["results"]), 1)
        store.assert_called_once()

    def test_empty_query_is_rejected(self):
        response = google_web_search.invoke({"query": "   "})
        self.assertIn("no puede estar vacía", response)


    def test_missing_knowledge_response_triggers_fallback(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertTrue(agent._response_needs_web_fallback(
            "No tengo información específica sobre el modelo Kimi-K3.", []
        ))

    def test_fallback_is_not_repeated_after_web_search(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        self.assertFalse(agent._response_needs_web_fallback(
            "No tengo información suficiente.", ["google_web_search"]
        ))

    def test_web_answer_hides_links_and_generic_attribution(self):
        agent = MachiningAgent.__new__(MachiningAgent)
        answer = agent._clean_web_answer(
            "Según [este artículo](https://example.com/kimi), Kimi K3 destaca en programación."
        )
        self.assertNotIn("http", answer)
        self.assertNotIn("Según", answer)
        self.assertIn("Kimi K3 destaca en programación", answer)


if __name__ == "__main__":
    unittest.main()
