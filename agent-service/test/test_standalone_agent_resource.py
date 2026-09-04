import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.system.resource_ingest import verify_and_sync_solidset_agent_mapping


class StandaloneAgentResourceTests(unittest.TestCase):
    def test_self_managed_resource_requires_active_model_and_verified_identity(self):
        resource_id = uuid4()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"exists": 1}
        cursor.rowcount = 1
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.__enter__.return_value = connection

        with (
            patch(
                "app.system.resource_ingest.read_active_resource_agent",
                return_value=None,
            ),
            patch(
                "app.system.resource_ingest.read_resource_identity",
                return_value={"ResourceId": str(resource_id)},
            ),
            patch(
                "app.system.resource_ingest._postgres_connection",
                side_effect=[connection, connection],
            ),
        ):
            result = verify_and_sync_solidset_agent_mapping(
                resource_id,
                resource_id,
                {"DataAPI": {"BaseUrl": "https://solidset.invalid"}},
            )

        self.assertTrue(result["verified"])
        self.assertTrue(result["matchesExpected"])
        self.assertEqual(result["IDAgentResource"], resource_id)

    def test_self_managed_resource_is_rejected_without_active_model(self):
        resource_id = uuid4()
        model_cursor = MagicMock()
        model_cursor.fetchone.return_value = None
        model_connection = MagicMock()
        model_connection.cursor.return_value.__enter__.return_value = model_cursor
        model_connection.__enter__.return_value = model_connection
        update_cursor = MagicMock()
        update_cursor.rowcount = 1
        update_connection = MagicMock()
        update_connection.cursor.return_value.__enter__.return_value = update_cursor
        update_connection.__enter__.return_value = update_connection

        with (
            patch(
                "app.system.resource_ingest.read_active_resource_agent",
                return_value=None,
            ),
            patch(
                "app.system.resource_ingest.read_resource_identity",
                return_value={"ResourceId": str(resource_id)},
            ),
            patch(
                "app.system.resource_ingest._postgres_connection",
                side_effect=[model_connection, update_connection],
            ),
        ):
            result = verify_and_sync_solidset_agent_mapping(
                resource_id,
                resource_id,
                {"DataAPI": {"BaseUrl": "https://solidset.invalid"}},
            )

        self.assertFalse(result["verified"])
        self.assertIsNone(result["IDAgentResource"])


if __name__ == "__main__":
    unittest.main()
