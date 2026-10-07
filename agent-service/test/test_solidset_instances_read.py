import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.controllers.solidset_instances import (
    read_solidset_instance,
    read_solidset_instance_agents,
    read_solidset_instances,
)


def instance_row(code="local-solidset", active=True):
    return {
        "ID": uuid4(),
        "Code": code,
        "Name": "SolidSET local",
        "BaseUrl": "http://localhost:52130",
        "NotificationUrl": "http://localhost:52131",
        "SourceIP": "localhost",
        "CountryCode": "PT",
        "Locale": "pt-PT",
        "TimeZone": "Europe/Lisbon",
        "active": active,
        "CreatedAt": datetime.now(timezone.utc),
        "UpdatedAt": datetime.now(timezone.utc),
        "DataAPI": {
            "BaseUrl": "http://solidset-data-api:8080",
            "EncryptedAPIKey": "must-not-leak",
            "TimeoutSeconds": 120,
            "MaxRows": 5000,
            "VerifyTLS": False,
            "active": True,
        },
    }


class TestSolidSETInstancesRead(unittest.TestCase):
    @patch("app.api.controllers.solidset_instances.list_active_solidset_instances")
    def test_list_returns_all_and_hides_credentials(self, list_instances):
        list_instances.return_value = [instance_row(), instance_row("inactive", False)]
        result = read_solidset_instances(activeOnly=False)
        list_instances.assert_called_once_with(active_only=False)
        self.assertEqual(2, result.total)
        self.assertTrue(result.items[0].DataAPI.APIKeyConfigured)
        self.assertFalse(hasattr(result.items[0].DataAPI, "EncryptedAPIKey"))

    @patch("app.api.controllers.solidset_instances.get_solidset_instance")
    def test_get_one_includes_inactive_and_hides_credentials(self, get_instance):
        get_instance.return_value = instance_row("inactive", False)
        result = read_solidset_instance(" inactive ")
        get_instance.assert_called_once_with(
            code="inactive",
            source_ip=None,
            active_only=False,
        )
        self.assertEqual("inactive", result.Code)
        self.assertFalse(result.active)
        self.assertTrue(result.DataAPI.APIKeyConfigured)

    @patch(
        "app.api.controllers.solidset_instances.get_solidset_instance",
        return_value=None,
    )
    def test_get_unknown_returns_404(self, _get_instance):
        with self.assertRaises(HTTPException) as raised:
            read_solidset_instance("missing")
        self.assertEqual(404, raised.exception.status_code)

    @patch("app.api.controllers.solidset_instances.get_agent_model_configurations")
    @patch("app.api.controllers.solidset_instances.get_active_agent_prompt")
    @patch("app.api.controllers.solidset_instances.get_agent_scope_profile")
    @patch("app.api.controllers.solidset_instances.get_active_agent_identity_for_resource")
    @patch("app.api.controllers.solidset_instances.list_active_agent_resource_ids")
    @patch("app.api.controllers.solidset_instances.get_solidset_instance")
    def test_agents_are_read_with_instance_scope(
        self,
        get_instance,
        list_resources,
        get_identity,
        get_profile,
        get_prompt,
        get_models,
    ):
        instance_id = uuid4()
        resource_id = uuid4()
        agent_resource_id = uuid4()
        get_instance.return_value = {
            "ID": instance_id,
            "Code": "local-developer",
        }
        list_resources.return_value = [resource_id]
        get_identity.return_value = {
            "IDAgentResource": agent_resource_id,
            "Name": "Developer senior",
        }
        get_profile.return_value = {"ScopeCount": 4}
        get_prompt.return_value = {
            "ID": uuid4(),
            "Version": 3,
            "Name": "Developer prompt",
            "PublishedAt": datetime.now(timezone.utc),
        }
        get_models.return_value = [{"ProviderCode": "ollama", "Priority": 10}]

        result = read_solidset_instance_agents(" local-developer ")

        get_instance.assert_called_once_with(
            code="local-developer", source_ip=None, active_only=False
        )
        list_resources.assert_called_once_with(instance_id)
        get_identity.assert_called_once_with(resource_id, instance_id)
        get_profile.assert_called_once_with(instance_id, resource_id)
        get_prompt.assert_called_once_with(instance_id, resource_id)
        get_models.assert_called_once_with(resource_id, instance_id)
        self.assertEqual(1, result["total"])
        self.assertEqual(resource_id, result["items"][0]["IDResource"])
        self.assertEqual(
            agent_resource_id, result["items"][0]["IDAgentResource"]
        )
        self.assertEqual("active", result["items"][0]["prompt"]["Status"])


if __name__ == "__main__":
    unittest.main()
