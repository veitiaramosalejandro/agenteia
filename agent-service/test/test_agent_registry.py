import unittest

from app.agent.contracts import AgentContext
from app.agent.registry import ToolRegistry


class FakeTool:
    def __init__(self):
        self.calls = []

    def invoke(self, arguments, config=None):
        self.calls.append((arguments, config))
        return "ok"


class ToolRegistryTests(unittest.TestCase):
    def test_preserves_mapping_behavior_and_filters_allowed_tools(self):
        first = object()
        second = object()
        registry = ToolRegistry({"first": first, "second": second})

        self.assertEqual(registry["first"], first)
        self.assertEqual(list(registry.values()), [first, second])
        self.assertEqual(registry.allowed({"second", "missing"}), [second])
        self.assertEqual(registry.names(), ("first", "second"))

    def test_register_rejects_empty_names(self):
        with self.assertRaises(ValueError):
            ToolRegistry().register("", object())

    def test_invoke_passes_agent_scope_only_to_external_web(self):
        web = FakeTool()
        regular = FakeTool()
        registry = ToolRegistry({"google_web_search": web, "regular": regular})
        context = AgentContext(agent_resource_id="agent-a")

        self.assertEqual(registry.invoke("google_web_search", {"query": "x"}, context=context), "ok")
        self.assertEqual(registry.invoke("regular", {"value": 1}, context=context), "ok")
        self.assertEqual(web.calls[0][1]["configurable"]["agent_resource_id"], "agent-a")
        self.assertIsNone(regular.calls[0][1])


if __name__ == "__main__":
    unittest.main()
