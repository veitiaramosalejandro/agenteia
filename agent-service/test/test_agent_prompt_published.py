import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.controllers.agent_prompts import router
from app.api.schemas.agent_prompts import AgentPromptStoredResponse
from app.services.agent_prompt_service import AgentPromptNotFound, get_published_prompt


class PublishedAgentPromptTests(unittest.TestCase):
    def test_published_database_shape_satisfies_endpoint_contract(self):
        now = datetime.now(timezone.utc)
        row = {
            "ID": uuid4(), "IDSolidSETInstance": uuid4(), "IDResource": uuid4(),
            "Version": 2, "Name": "Prompt", "SystemPrompt": "Contenido",
            "BehaviorConfig": {}, "Status": "active", "CreatedBy": "manual",
            "CreatedAt": now, "PublishedAt": now, "RetiredAt": None,
        }
        self.assertEqual("active", AgentPromptStoredResponse(**row).Status)

    def test_service_reads_active_prompt_from_selected_instance(self):
        instance_id, resource_id = uuid4(), uuid4()
        expected = {"ID": uuid4(), "Status": "active"}
        with patch("app.services.agent_prompt_service.get_solidset_instance", return_value={"ID": instance_id}), \
             patch("app.services.agent_prompt_service.get_active_agent_prompt", return_value=expected) as loader:
            self.assertEqual(expected, get_published_prompt("beta-solidset", resource_id))
        loader.assert_called_once_with(instance_id, resource_id)

    def test_missing_published_prompt_is_not_replaced_by_a_draft(self):
        with patch("app.services.agent_prompt_service.get_solidset_instance", return_value={"ID": uuid4()}), \
             patch("app.services.agent_prompt_service.get_active_agent_prompt", return_value=None):
            with self.assertRaises(AgentPromptNotFound):
                get_published_prompt("beta-solidset", uuid4())

    def test_endpoint_returns_behavior_needed_to_fill_editor(self):
        resource_id, instance_id, prompt_id = uuid4(), uuid4(), uuid4()
        stored = {
            "ID": prompt_id, "IDSolidSETInstance": instance_id, "IDResource": resource_id,
            "Version": 4, "Name": "CEO Estratégico", "SystemPrompt": "IDENTIDADE",
            "BehaviorConfig": {"role": "Director ejecutivo", "specialties": ["Estrategia"]},
            "Status": "active", "CreatedBy": "manual", "CreatedAt": datetime.now(timezone.utc),
            "PublishedAt": datetime.now(timezone.utc), "RetiredAt": None,
        }
        app = FastAPI(); app.include_router(router)
        with patch("app.api.controllers.agent_prompts.get_published_prompt", return_value=stored):
            response = TestClient(app).get(
                f"/api/v1/agent/solidset/agents/{resource_id}/prompt/published",
                params={"instanceCode": "beta-solidset"},
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual("Director ejecutivo", response.json()["BehaviorConfig"]["role"])


if __name__ == "__main__":
    unittest.main()
