import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.tools import google_web_search


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


if __name__ == "__main__":
    unittest.main()
