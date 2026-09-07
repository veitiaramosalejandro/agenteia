import unittest

from app.agent.contracts import AgentContext, ToolAuditEvent, ToolPolicy
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
            "web", ToolPolicy(
                source="external_web", learn_result=True,
                learning_scope="agent", confidence=0.75,
            ),
        )

        result = registry.invoke_result("web", {"query": "x"})

        self.assertEqual(result.content, "ok")
        self.assertEqual(result.source, "external_web")
        self.assertEqual(result.confidence, 0.75)
        self.assertFalse(result.verified)
        self.assertTrue(result.metadata["learn_result"])
        self.assertEqual(result.metadata["learning_scope"], "agent")
        self.assertEqual(result.metadata["tool_name"], "web")

    def test_required_permission_is_enforced_when_declared(self):
        tool = FakeTool()
        registry = ToolRegistry({"protected": tool})
        registry.set_policy(
            "protected", ToolPolicy(required_permission="read:protected")
        )

        with self.assertRaises(PermissionError):
            registry.invoke("protected", context=AgentContext())
        self.assertEqual(
            registry.invoke(
                "protected",
                context=AgentContext(metadata={"tool_permissions": ["read:protected"]}),
            ),
            "ok",
        )

        with self.assertRaises(PermissionError):
            registry.invoke(
                "protected",
                context=AgentContext(metadata={"tool_permissions": ["read:other"]}),
            )

    def test_invoke_result_emits_success_audit_without_changing_content(self):
        tool = FakeTool()
        events = []
        registry = ToolRegistry({"web": tool})
        registry.set_policy("web", ToolPolicy(source="external_web"))
        registry.set_auditor(events.append)

        result = registry.invoke_result(
            "web", context=AgentContext(agent_resource_id="agent-a", session_id="s1")
        )

        self.assertEqual(result.content, "ok")
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ToolAuditEvent)
        self.assertTrue(events[0].success)
        self.assertEqual(events[0].agent_resource_id, "agent-a")
        self.assertEqual(events[0].session_id, "s1")
        self.assertGreaterEqual(events[0].elapsed_seconds, 0)


if __name__ == "__main__":
    unittest.main()
