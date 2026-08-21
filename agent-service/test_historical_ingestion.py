import unittest
from uuid import uuid4
from unittest.mock import Mock, patch

from app.historical.normalizer import normalize_historical_message
from app.historical.producer import enqueue_next_batch
from app.historical.store import set_cursor
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

    @patch("app.historical.producer.HistoricalQueue")
    @patch("app.historical.producer.upsert_audit")
    @patch("app.historical.producer.set_cursor")
    @patch("app.historical.producer.extract_batch")
    @patch("app.historical.producer.get_cursor")
    @patch("app.historical.producer.ensure_schema")
    def test_recovering_cursor_continues_after_last_checkpoint(
        self, _ensure, get_cursor, extract_batch, save_cursor, _audit, queue_type,
    ):
        instance_id = str(uuid4())
        get_cursor.return_value = {
            "LastIDChat2": 900,
            "LastStamp": None,
            "Status": "recovering",
        }
        extract_batch.return_value = [
            {"IDChat2": 901, "Stamp": "2026-08-21T10:00:00Z"},
            {"IDChat2": 902, "Stamp": "2026-08-21T10:01:00Z"},
        ]

        result = enqueue_next_batch({"ID": instance_id, "Code": "pt"}, False)

        extract_batch.assert_called_once()
        self.assertEqual(900, extract_batch.call_args.args[0])
        self.assertEqual("queued", result["status"])
        self.assertEqual(f"{instance_id}:901:902", result["batchId"])
        self.assertEqual(result["batchId"], save_cursor.call_args.kwargs["batch_id"])
        queue_type.return_value.enqueue.assert_called_once()

    def test_cursor_update_is_monotonic(self):
        executed = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params):
                executed["sql"] = sql
                executed["params"] = params

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("app.historical.store.connection", return_value=FakeConnection()):
            set_cursor(str(uuid4()), 902, None, "completed")

        self.assertIn('GREATEST("LastIDChat2", %s)', executed["sql"])
        self.assertIn('CASE WHEN %s >= "LastIDChat2"', executed["sql"])


if __name__ == "__main__":
    unittest.main()
