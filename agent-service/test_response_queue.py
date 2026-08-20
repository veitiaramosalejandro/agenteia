import json
import unittest
from unittest.mock import MagicMock, patch

from app.response_queue import AgentResponseQueue


class AgentResponseQueueTests(unittest.TestCase):
    @patch("app.response_queue.redis.Redis.from_url")
    def test_enqueue_uses_chat_id_and_framework_payload(self, from_url):
        client = MagicMock()
        client.xadd.return_value = "1-0"
        from_url.return_value = client
        queue = AgentResponseQueue()

        stream_id = queue.enqueue(
            "1824918",
            "1824918",
            {"Chat": {"idChat2": 1824918}},
            {"ID": "instance-1", "BaseUrl": "http://solidset"},
        )

        self.assertEqual(stream_id, "1-0")
        fields = client.xadd.call_args.args[1]
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


if __name__ == "__main__":
    unittest.main()
