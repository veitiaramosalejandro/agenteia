import unittest
from unittest.mock import patch
from uuid import uuid4

from app.api.schemas.agent_prompts import AgentPromptGenerateRequest
from app.services.agent_prompt_service import generate_active_prompt_drafts


class AgentPromptBulkTests(unittest.TestCase):
    @patch("app.services.agent_prompt_service.create_agent_prompt_draft")
    @patch("app.services.agent_prompt_service.get_latest_agent_prompt")
    @patch("app.services.agent_prompt_service.get_agent_scope_profile")
    @patch("app.services.agent_prompt_service.list_active_agent_resource_ids")
    @patch("app.services.agent_prompt_service.get_solidset_instance")
    def test_generates_only_changed_prompts(
        self, get_instance, list_resources, get_profile, get_latest, create_draft,
    ):
        instance_id = uuid4()
        first, second = uuid4(), uuid4()
        get_instance.return_value = {"ID": instance_id}
        list_resources.return_value = [first, second]
        get_profile.return_value = {
            "DisplayName": "Dev17", "OrganizationName": "ROBOTEA",
        }
        request = AgentPromptGenerateRequest(specialties=[])

        # The first resource already has the exact generated content.
        from app.agent.prompt_generator import generate_agent_system_prompt
        behavior = request.model_dump(exclude={"name", "created_by"})
        expected_prompt = generate_agent_system_prompt(get_profile.return_value, behavior)
        existing_id = uuid4()
        get_latest.side_effect = [
            {"ID": existing_id, "Version": 2, "SystemPrompt": expected_prompt,
             "BehaviorConfig": behavior},
            None,
        ]
        new_id = uuid4()
        create_draft.return_value = {"ID": new_id, "Version": 1}

        result = generate_active_prompt_drafts("local-solidset", request)

        self.assertEqual(2, result.activeAgents)
        self.assertEqual(1, result.generated)
        self.assertEqual(1, result.unchanged)
        self.assertEqual(0, result.failed)
        create_draft.assert_called_once()


if __name__ == "__main__":
    unittest.main()
