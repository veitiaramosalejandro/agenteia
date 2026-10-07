import json
import unittest
from unittest.mock import MagicMock, patch
import redis

from app.response_queue import AgentResponseQueue


class AgentResponseQueueTests(unittest.TestCase):
    def test_heartbeat_refreshes_while_job_runs_and_stops_on_exit(self):
        queue = AgentResponseQueue.__new__(AgentResponseQueue)
        queue.refresh_pending = MagicMock(return_value=True)
        with (
            patch("app.response_queue.threading.Event") as event_type,
            patch("app.response_queue.threading.Thread") as thread_type,
        ):
            event_type.return_value.wait.side_effect = [False, True]
            with queue.keep_pending_alive("worker-1", "1-0") as owned:
                self.assertTrue(owned)
                thread_type.call_args.kwargs["target"]()
                self.assertEqual(2, queue.refresh_pending.call_count)
                event_type.return_value.set.assert_not_called()
            event_type.return_value.set.assert_called_once()
            thread_type.return_value.join.assert_called_once()

    def test_stale_worker_does_not_start_processing(self):
        queue = AgentResponseQueue.__new__(AgentResponseQueue)
        queue.refresh_pending = MagicMock(return_value=False)
        with patch("app.response_queue.threading.Thread") as thread_type:
            with queue.keep_pending_alive("worker-1", "1-0") as owned:
                self.assertFalse(owned)
            thread_type.assert_not_called()

    def test_heartbeat_stops_after_processing_exception(self):
        queue = AgentResponseQueue.__new__(AgentResponseQueue)
        queue.refresh_pending = MagicMock(return_value=True)
        with (
            patch("app.response_queue.threading.Event") as event_type,
            patch("app.response_queue.threading.Thread") as thread_type,
        ):
            with self.assertRaises(RuntimeError):
                with queue.keep_pending_alive("worker-1", "1-0"):
                    raise RuntimeError("processing failed")
            event_type.return_value.set.assert_called_once()
            thread_type.return_value.join.assert_called_once()

    @patch("app.response_queue.redis.Redis.from_url")
    def test_enqueue_uses_chat_id_and_framework_payload(self, from_url):
        client = MagicMock()
        client.eval.return_value = "1-0"
        from_url.return_value = client
        queue = AgentResponseQueue()

        stream_id = queue.enqueue(
            "1824918",
            "1824918",
            {"Chat": {"idChat2": 1824918}},
            {"ID": "instance-1", "BaseUrl": "http://solidset"},
        )

        self.assertEqual(stream_id, "1-0")
        arguments = client.eval.call_args.args[4:]
        fields = dict(zip(arguments[::2], arguments[1::2]))
        self.assertEqual(fields["request_id"], "1824918")
        self.assertEqual(json.loads(fields["payload"])["Chat"]["idChat2"], 1824918)
        self.assertEqual(json.loads(fields["instance"])["ID"], "instance-1")

    @patch("app.response_queue.redis.Redis.from_url")
    def test_read_claims_abandoned_message_before_new_messages(self, from_url):
        client = MagicMock()
        client.xautoclaim.return_value = (
            "0-0",
            [("2-0", {"request_id": "1824918"})],
            [],
        )
        from_url.return_value = client
        queue = AgentResponseQueue()

        messages = queue.read("worker-1")

        self.assertEqual(messages[0][0], "2-0")
        client.xreadgroup.assert_not_called()

    @patch("app.response_queue.redis.Redis.from_url")
    def test_blocking_read_timeout_is_an_empty_queue(self, from_url):
        client = MagicMock()
        client.xautoclaim.return_value = ("0-0", [], [])
        client.xreadgroup.side_effect = redis.TimeoutError("Timeout reading from socket")
        from_url.return_value = client
        queue = AgentResponseQueue()

        self.assertEqual(queue.read("worker-1"), [])


if __name__ == "__main__":
    unittest.main()
