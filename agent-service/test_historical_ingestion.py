import unittest
from contextlib import contextmanager
from uuid import uuid4
from unittest.mock import Mock, patch

from app.historical.normalizer import normalize_historical_message, normalize_historical_task
from app.historical.producer import enqueue_next_batch
from app.historical.extractor import extract_agent_chat_batch, extract_agent_task_batch
from app.historical.store import set_cursor
from app.historical.worker import _document, process_batch


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
    @patch("app.historical.producer.extract_agent_chat_batch")
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

        agent = {
            "IDResource": str(uuid4()),
            "IDAgentResource": str(uuid4()),
            "WorkRooms": [],
        }
        result = enqueue_next_batch(
            {"ID": instance_id, "Code": "pt"}, False, agent,
        )

        extract_batch.assert_called_once()
        self.assertEqual(900, extract_batch.call_args.args[0])
        self.assertEqual("queued", result["status"])
        self.assertEqual(
            f"{instance_id}:{agent['IDResource']}:chat:901:902",
            result["batchId"],
        )
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

    def test_normalizes_task_without_requiring_workroom(self):
        resource_id = str(uuid4())
        normalized, reason = normalize_historical_task({
            "IDTask": 4512,
            "RawMessage": "T-10 | Revisar manutenção preventiva | Em curso",
            "Stamp": "2026-08-21T10:00:00Z",
            "IDWorkRoom": None,
        }, resource_id)

        self.assertIsNone(reason)
        self.assertEqual(4512, normalized["IDChat2"])
        self.assertEqual("task", normalized["SourceType"])
        document = _document(
            normalized, str(uuid4()), "task",
            {"IDResource": resource_id, "IDAgentResource": str(uuid4())},
        )
        self.assertIsNone(document["IDWorkRoom"])
        self.assertEqual("task", document["SourceType"])
        self.assertEqual("4512", document["SourceID"])

    @patch("app.historical.worker.upsert_audit")
    @patch("app.historical.worker.set_cursor")
    @patch("app.historical.worker.historical_agent_is_active", return_value=False)
    def test_pending_batch_is_rejected_when_agent_was_deactivated(
        self, _active, save_cursor, audit,
    ):
        batch = {
            "batchId": "inactive-agent-batch",
            "instanceId": str(uuid4()),
            "resourceId": str(uuid4()),
            "agentResourceId": str(uuid4()),
            "sourceType": "chat",
            "cursorSource": "solidset_chat_history:test",
            "firstIdChat2": 10,
            "lastIdChat2": 10,
            "messages": [self._row(IDChat2=10)],
        }

        result = process_batch(batch)

        self.assertEqual(0, result["indexed"])
        self.assertEqual("inactive", audit.call_args.args[1])
        self.assertEqual("inactive", save_cursor.call_args_list[-1].args[3])

    def test_chat_without_idmeeting_uses_null_expression(self):
        executed = []

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def execute(self, sql, params=()): executed.append((sql, params))
            def fetchone(self): return None
            def fetchall(self): return []

        class Connection:
            def cursor(self, **_kwargs): return Cursor()

        @contextmanager
        def fake_connection(*_args, **_kwargs):
            yield Connection()

        with patch("app.historical.extractor.connect_solidset_sql", fake_connection):
            extract_agent_chat_batch(0, 10, str(uuid4()), [], {"ID": str(uuid4())})

        self.assertIn("NULL AS IDMeeting", executed[1][0])
        self.assertNotIn("c.IDMeeting AS IDMeeting", executed[1][0])

    def test_task_ignores_non_uuid_resource_columns(self):
        executed = []

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def execute(self, sql, params=()): executed.append((sql, params))
            def fetchall(self):
                if len(executed) == 1:
                    return [
                        {"TABLE_NAME": "SysTask", "COLUMN_NAME": "IDTask", "DATA_TYPE": "int"},
                        {"TABLE_NAME": "SysTask", "COLUMN_NAME": "IDResource", "DATA_TYPE": "tinyint"},
                        {"TABLE_NAME": "SysTask", "COLUMN_NAME": "IDOwnerResource", "DATA_TYPE": "uniqueidentifier"},
                        {"TABLE_NAME": "SysTask", "COLUMN_NAME": "Name", "DATA_TYPE": "nvarchar"},
                    ]
                return []

        class Connection:
            def cursor(self, **_kwargs): return Cursor()

        @contextmanager
        def fake_connection(*_args, **_kwargs):
            yield Connection()

        resource = str(uuid4())
        with patch("app.historical.extractor.connect_solidset_sql", fake_connection):
            extract_agent_task_batch(0, 10, resource, {"ID": str(uuid4())})

        query, params = executed[1]
        self.assertNotIn("t.[IDResource]=%s", query)
        self.assertIn("t.[IDOwnerResource]=%s", query)
        self.assertEqual((10, 0, resource), params)


if __name__ == "__main__":
    unittest.main()
