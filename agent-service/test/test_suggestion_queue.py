import json
import unittest
from unittest.mock import MagicMock, patch

from app.suggestion_queue import SuggestionQueue


class SuggestionQueueTests(unittest.TestCase):
    @patch("app.suggestion_queue.redis.Redis.from_url")
    def test_enqueue_persists_payload_instance_and_request(self, from_url):
        client = MagicMock()
        client.xadd.return_value = "1-0"
        from_url.return_value = client
        queue = SuggestionQueue()

        stream_id = queue.enqueue(
            "1834117469",
            {"Chat": {"idChat2": 1834117469}},
            {"ID": "instance-1"},
        )

        self.assertEqual("1-0", stream_id)
        fields = client.xadd.call_args.args[1]
        self.assertEqual("1834117469", fields["request_id"])
        self.assertEqual(1834117469, json.loads(fields["payload"])["Chat"]["idChat2"])
        self.assertEqual("instance-1", json.loads(fields["instance"])["ID"])

    @patch("app.suggestion_queue.redis.Redis.from_url")
    def test_read_claims_abandoned_work_before_new_work(self, from_url):
        client = MagicMock()
        client.xautoclaim.return_value = (
            "0-0", [("2-0", {"request_id": "1834117469"})], []
        )
        from_url.return_value = client
        queue = SuggestionQueue()

        self.assertEqual("2-0", queue.read("worker-1")[0][0])
        client.xreadgroup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
