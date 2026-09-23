import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.system.resource_ingest import ingest_solidset_resources


class ResourceIngestTests(unittest.TestCase):
    @patch("app.system.resource_ingest._postgres_connection")
    @patch("app.system.resource_ingest.read_dataset")
    def test_reactivates_existing_base_resource(self, read_dataset, connection):
        instance_id = uuid4()
        resource_id = uuid4()
        agent_resource_id = uuid4()
        read_dataset.return_value = [{
            "ResourceId": str(resource_id),
            "DisplayName": "Agente Financeiro",
            "IDAgentResource": str(agent_resource_id),
        }]
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"IDResource": resource_id}]
        connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        result = ingest_solidset_resources({
            "ID": instance_id,
            "DataAPI": {"BaseUrl": "https://data.invalid"},
        })

        base_upsert_sql = cursor.executemany.call_args_list[0].args[0]
        self.assertIn('"IDSolidSETInstance", active', base_upsert_sql)
        self.assertIn("active = true", base_upsert_sql)
        self.assertEqual(1, result["synchronized"])
        self.assertEqual(1, result["updated"])


if __name__ == "__main__":
    unittest.main()
