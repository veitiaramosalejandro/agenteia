import unittest
from unittest.mock import patch

from app.services.code_context import (
    _ZERO_UUID,
    attach_code_context,
    is_code_reference,
    looks_like_code,
    stable_conversation_session_id,
)


class CodeContextTests(unittest.TestCase):
    def test_detects_html_code_and_reference(self):
        code = "<!DOCTYPE html>\n<html><head><title>Demo</title></head><body><script>const value = 1; function check() { return value; }</script></body></html>"
        self.assertTrue(looks_like_code(code))
        self.assertTrue(is_code_reference("Analiza el codigo anterior y dime los errores"))
        self.assertTrue(looks_like_code("&lt;!DOCTYPE html&gt; &lt;html&gt;&lt;head&gt;&lt;title&gt;Demo&lt;/title&gt;&lt;/head&gt;&lt;body&gt;const value = 1;&lt;/body&gt;&lt;/html&gt;"))

    def test_zero_solidset_session_is_replaced_by_stable_scope(self):
        metadata = {
            "session_id": str(_ZERO_UUID),
            "solidset_instance_id": "instance",
            "agent_resource_id": "agent",
            "workroom_id": "room",
            "requester_resource_id": "human",
        }
        first = stable_conversation_session_id(metadata)
        second = stable_conversation_session_id({**metadata, "session_id": ""})
        self.assertEqual(first, second)
        self.assertNotEqual(first, str(_ZERO_UUID))

    @patch("app.services.code_context.store_code_context")
    @patch("app.services.code_context.load_code_context")
    def test_followup_attaches_previous_code(self, load, store):
        code = "<html><body><script>const value = 1; function check() { return value; }</script></body></html>"
        store.return_value = {"content": code, "sha256": "hash"}
        metadata = {}
        attach_code_context(code, metadata, "session")
        self.assertEqual(metadata["code_reference_mode"], True)
        load.return_value = {"content": code, "sha256": "hash", "chat_id": "1"}
        followup = {}
        attach_code_context("Analiza el codigo anterior", followup, "session")
        self.assertEqual(followup["code_context"], code)
        self.assertEqual(followup["model_capability"], "coding")


if __name__ == "__main__":
    unittest.main()
