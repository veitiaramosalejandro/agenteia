import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.system.resource_ingest import ingest_solidset_agent_scopes


class AgentScopeIngestTests(unittest.TestCase):
    @patch("app.system.resource_ingest._postgres_connection")
    @patch("app.system.resource_ingest.read_dataset")
    def test_materializes_complete_scope_with_numeric_access(self, read_dataset, connection):
        instance_id = uuid4()
        resource_id = uuid4()
        login_id = uuid4()
        workroom_id = uuid4()
        community_id = uuid4()
        read_dataset.return_value = [{
            "ResourceId": str(resource_id),
            "IDLogin": str(login_id),
            "IDWorkRoom": str(workroom_id),
            "IDCommunity": str(community_id),
            "ResourceAccessType": 2,
            "DisplayName": "Agente técnico",
            "FullName": "Utilizador",
            "WorkRoomCode": "SUP",
            "WorkRoomName": "Suporte",
            "CommunityCode": "OPS",
            "CommunityName": "Operações",
            "CommunityDescription": "Suporte operacional",
            "organizationid": "org-1",
            "organization_no": "100",
            "accountname": "Empresa",
        }]
        cursor = MagicMock()
        connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        result = ingest_solidset_agent_scopes({
            "ID": instance_id,
            "DataAPI": {"BaseUrl": "https://data.invalid"},
        })

        self.assertEqual(1, result["synchronized"])
        self.assertEqual(0, result["skipped"])
        read_dataset.assert_called_once_with(
            {"BaseUrl": "https://data.invalid"}, "agent-scopes"
        )
        values = cursor.executemany.call_args.args[1][0]
        self.assertEqual(instance_id, values[0])
        self.assertEqual(resource_id, values[1])
        self.assertEqual(2, values[-3])
        self.assertEqual(64, len(values[-2]))

    @patch("app.system.resource_ingest._postgres_connection")
    @patch("app.system.resource_ingest.read_dataset")
    def test_rejects_access_outside_enum(self, read_dataset, connection):
        read_dataset.return_value = [{
            "ResourceId": str(uuid4()), "IDLogin": str(uuid4()),
            "IDWorkRoom": str(uuid4()), "IDCommunity": str(uuid4()),
            "ResourceAccessType": 7,
        }]
        cursor = MagicMock()
        connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        result = ingest_solidset_agent_scopes({"ID": uuid4(), "DataAPI": {}})
        self.assertEqual(0, result["synchronized"])
        self.assertEqual(1, result["skipped"])
        cursor.executemany.assert_not_called()


if __name__ == "__main__":
    unittest.main()
