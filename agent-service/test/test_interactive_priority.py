import unittest
from unittest.mock import patch

from app.agent.runtime_prompts import runtime_prompt
from app.interactive_priority import interactive_work, wait_for_interactive_idle
from app.system.system_knowledge_worker import _retry_delay


class FakeRedis:
    def __init__(self):
        self.added = []
        self.removed = []

    def zadd(self, key, values):
        self.added.append((key, values))

    def zrem(self, key, value):
        self.removed.append((key, value))


class TestInteractivePriority(unittest.TestCase):
    def test_lease_is_registered_and_removed(self):
        client = FakeRedis()
        with patch("app.interactive_priority._client", return_value=client), patch(
            "app.interactive_priority.settings.INTERACTIVE_PRIORITY_ENABLED", True
        ):
            with interactive_work("test"):
                self.assertEqual(1, len(client.added))
        self.assertEqual(1, len(client.removed))
        self.assertEqual(next(iter(client.added[0][1])), client.removed[0][1])

    def test_ingestion_waits_only_while_interactive_work_exists(self):
        with patch(
            "app.interactive_priority.settings.INGESTION_PAUSE_DURING_INTERACTIVE", True
        ), patch(
            "app.interactive_priority.interactive_work_active",
            side_effect=[True, True, False],
        ), patch("app.interactive_priority.time.sleep") as sleep:
            wait_for_interactive_idle()
        self.assertEqual(2, sleep.call_count)

    def test_runtime_prompt_is_native_and_compact_in_each_language(self):
        markers = {"pt": "És o assistente", "es": "Eres el asistente", "en": "You are the"}
        for language, marker in markers.items():
            prompt = runtime_prompt(language)
            self.assertIn(marker, prompt)
            self.assertLess(len(prompt), 2200)
            self.assertIn("SELECT", prompt)

    def test_runtime_prompts_preserve_twin_identity_in_every_language(self):
        required_markers = {
            "es": ("gemelo digital", "primera persona", "tercera persona", "memoria persistente aislada"),
            "pt": ("gémeo digital", "primeira pessoa", "terceira pessoa", "memória persistente isolada"),
            "en": ("digital twin", "first person", "third person", "isolated persistent memory"),
        }
        for language, markers in required_markers.items():
            prompt = runtime_prompt(language)
            for marker in markers:
                with self.subTest(language=language, marker=marker):
                    self.assertIn(marker, prompt)

    def test_embedding_restart_uses_short_retry_delay(self):
        with patch(
            "app.system.system_knowledge_worker.settings.SYSTEM_KNOWLEDGE_RETRY_SECONDS", 60
        ):
            self.assertEqual(60, _retry_delay(RuntimeError(
                "Server disconnected without sending a response."
            ), 20))
            self.assertEqual(900, _retry_delay(RuntimeError("invalid document"), 20))


if __name__ == "__main__":
    unittest.main()
