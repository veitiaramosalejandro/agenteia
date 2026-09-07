import unittest

from app.agent.registry import ToolRegistry


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


if __name__ == "__main__":
    unittest.main()
