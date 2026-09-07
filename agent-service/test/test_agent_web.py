import unittest
from unittest.mock import Mock

from app.agent.web import AgentWeb


class AgentWebTests(unittest.TestCase):
    def test_search_forwards_agent_scope_to_web_tool(self):
        tool = Mock()
        tool.invoke.return_value = '{"answer":"resultado"}'
        web = AgentWeb(tool)

        result = web.search("consulta", agent_resource_id="agent-a")

        self.assertEqual(result, '{"answer":"resultado"}')
        tool.invoke.assert_called_once_with(
            {"query": "consulta"},
            config={"configurable": {"agent_resource_id": "agent-a"}},
        )


if __name__ == "__main__":
    unittest.main()
