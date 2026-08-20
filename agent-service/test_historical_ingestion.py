import unittest
from uuid import uuid4

from app.historical.normalizer import normalize_historical_message
from app.historical.worker import _document


class HistoricalIngestionTests(unittest.TestCase):
    def _row(self, **changes):
        row = {
            "IDChat2": 1824911,
            "IDSenderResource": str(uuid4()),
            "IDWorkRoom": str(uuid4()),
            "RawMessage": "La entrega del proyecto será el viernes.",
            "GeneratedByIA": 0,
            "FullName": "Alejandro Veitia",
        }
        row.update(changes)
        return row

    def test_normalizes_human_message(self):
        normalized, reason = normalize_historical_message(self._row())
        self.assertIsNone(reason)
        self.assertEqual(normalized["NormalizedText"], "La entrega del proyecto será el viernes.")
        self.assertEqual(len(normalized["ContentHash"]), 64)

    def test_rejects_generated_ai_and_secrets(self):
        self.assertEqual(
            normalize_historical_message(self._row(GeneratedByIA=1))[1],
            "generated_by_ia",
        )
        self.assertEqual(
            normalize_historical_message(self._row(RawMessage="password: SuperSecret123"))[1],
            "sensitive",
        )

    def test_document_id_is_deterministic_and_isolated_by_agent(self):
        normalized, _ = normalize_historical_message(self._row())
        instance = str(uuid4())
        agent_a = {"IDResource": str(uuid4()), "IDAgentResource": str(uuid4())}
        agent_b = {"IDResource": str(uuid4()), "IDAgentResource": str(uuid4())}
        first = _document(normalized, instance, "workroom", agent_a)
        repeated = _document(normalized, instance, "workroom", agent_a)
        other = _document(normalized, instance, "workroom", agent_b)
        self.assertEqual(first["DocumentID"], repeated["DocumentID"])
        self.assertNotEqual(first["DocumentID"], other["DocumentID"])
        self.assertEqual(first["payload"]["agent_resource_id"], agent_a["IDResource"])


if __name__ == "__main__":
    unittest.main()
