import json
import unittest
from unittest.mock import MagicMock, patch

from app.agent.tools import solidset_send_chat_message
from app.solidset_retry_queue import SolidSETRetryQueue


class SolidSETRetryQueueTests(unittest.TestCase):
    @patch("app.solidset_retry_queue.SolidSETRetryQueue.enqueue", return_value="delivery-1")
    @patch("app.agent.tools._solidset_request_authenticated", return_value=(None, "", "connection refused"))
    @patch("app.agent.tools.settings.SOLIDSET_USER_ACTIONS_ENABLED", True)
    @patch("app.agent.tools.settings.SOLIDSET_RETRY_QUEUE_ENABLED", True)
    def test_connection_failure_enqueues_final_message(self, _request, enqueue):
        result = solidset_send_chat_message.invoke({
            "canal_id": "channel-1", "mensaje": "respuesta final", "confirm": True,
        })

        self.assertTrue(str(result).startswith("🕒"))
        arguments = enqueue.call_args.args[0]
        self.assertEqual(arguments["mensaje"], "respuesta final")
        self.assertFalse(arguments["preview_only"])

    @patch("app.solidset_retry_queue.time.time", return_value=1000.0)
    @patch("app.solidset_retry_queue.redis.Redis.from_url")
    def test_enqueue_is_due_for_next_worker_cycle(self, from_url, _time):
        client = MagicMock()
        from_url.return_value = client
        queue = SolidSETRetryQueue()

        item_id = queue.enqueue({"mensaje": "hola"}, error="connection refused")

        raw, score = next(iter(client.zadd.call_args.args[1].items()))
        item = json.loads(raw)
        self.assertEqual(item["id"], item_id)
        self.assertEqual(item["arguments"]["mensaje"], "hola")
        self.assertEqual(score, 1000.0)

    @patch("app.solidset_retry_queue.redis.Redis.from_url")
    def test_claim_due_moves_item_to_processing(self, from_url):
        item = {"id": "delivery-1", "arguments": {"mensaje": "hola"}}
        raw = json.dumps(item)
        pipeline = MagicMock()
        pipeline.__enter__.return_value = pipeline
        pipeline.__exit__.return_value = False
        pipeline.zscore.return_value = 1.0
        pipeline.execute.return_value = [1, 1]
        client = MagicMock()
        client.zrangebyscore.return_value = [raw]
        client.pipeline.return_value = pipeline
        from_url.return_value = client

        claimed = SolidSETRetryQueue().claim_due()

        self.assertEqual(claimed, [item])
        pipeline.hset.assert_called_once_with(
            "machining:solidset-deliveries:v1:processing", "delivery-1", raw
        )


if __name__ == "__main__":
    unittest.main()
