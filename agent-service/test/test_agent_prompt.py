import unittest
from unittest.mock import patch
from uuid import uuid4

from app.agent.core import MachiningAgent


class AgentPromptTests(unittest.TestCase):
    @patch("app.agent.core.get_active_agent_prompt")
    def test_prompt_cache_is_scoped_by_instance_and_resource(self, loader):
        loader.return_value = {
            "Name": "Suporte", "Version": 1, "SystemPrompt": "Seja direto."
        }
        agent = MachiningAgent.__new__(MachiningAgent)
        agent.agent_prompt_cache = {}
        instance_id = str(uuid4())
        resource_id = str(uuid4())

        first = agent._get_active_agent_prompt_cached(instance_id, resource_id)
        second = agent._get_active_agent_prompt_cached(instance_id, resource_id)

        self.assertEqual(first, second)
        loader.assert_called_once_with(instance_id, resource_id)

    @patch("app.agent.core.get_active_agent_prompt")
    def test_different_instances_never_share_prompt_cache(self, loader):
        loader.side_effect = [
            {"Name": "Instância A", "Version": 1},
            {"Name": "Instância B", "Version": 1},
        ]
        agent = MachiningAgent.__new__(MachiningAgent)
        agent.agent_prompt_cache = {}
        resource_id = str(uuid4())

        prompt_a = agent._get_active_agent_prompt_cached(str(uuid4()), resource_id)
        prompt_b = agent._get_active_agent_prompt_cached(str(uuid4()), resource_id)

        self.assertNotEqual(prompt_a["Name"], prompt_b["Name"])
        self.assertEqual(2, loader.call_count)


if __name__ == "__main__":
    unittest.main()
