import unittest

from app.agent.contracts import AgentContext, ToolPolicy
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
        registry = ToolRegistry({"contextual": web, "regular": regular})
        registry.set_policy("contextual", ToolPolicy(requires_agent_context=True))
        context = AgentContext(agent_resource_id="agent-a")

        self.assertEqual(registry.invoke("contextual", {"query": "x"}, context=context), "ok")
        self.assertEqual(registry.invoke("regular", {"value": 1}, context=context), "ok")
        self.assertEqual(web.calls[0][1]["configurable"]["agent_resource_id"], "agent-a")
        self.assertIsNone(regular.calls[0][1])

    def test_invoke_result_keeps_content_and_adds_provenance(self):
        tool = FakeTool()
        registry = ToolRegistry({"web": tool})
        registry.set_policy(
            "web",
            ToolPolicy(source="external_web", learn_result=True),
        )

        result = registry.invoke_result("web", {"query": "x"})

        self.assertEqual(result.content, "ok")
        self.assertEqual(result.source, "external_web")
        self.assertFalse(result.verified)
        self.assertTrue(result.metadata["learn_result"])
        self.assertEqual(result.metadata["tool_name"], "web")


if __name__ == "__main__":
    unittest.main()
