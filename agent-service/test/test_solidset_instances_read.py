import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.controllers.solidset_instances import (
    read_solidset_instance,
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


if __name__ == "__main__":
    unittest.main()
