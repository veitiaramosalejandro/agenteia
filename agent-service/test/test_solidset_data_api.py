import unittest
from unittest.mock import MagicMock, Mock, patch

from app.connectors.solidset_data_api import (
    DataAPIConnection,
    SolidSETDataAPIError,
    _read_legacy_agent_scopes,
    _runtime_base_url,
    _strip_sql_comments,
    read_dataset,
    read_schema_catalog,
)
from app.connectors.solidset_sql import connect as connect_solidset_data


class SolidSETDataAPIConnectorTests(unittest.TestCase):
    @patch("app.connectors.solidset_data_api.DataAPIConnection")
    @patch("app.connectors.solidset_data_api._read_legacy_agent_scopes")
    def test_only_missing_scope_dataset_uses_compatibility(self, fallback, connection_type):
        connection = connection_type.return_value.__enter__.return_value
        connection.max_rows = 1000
        response = connection.client.get.return_value
        response.status_code = 404
        response.json.return_value = {"detail": "O conjunto de dados não existe."}
        fallback.return_value = [{"ResourceId": "resource"}]
        self.assertEqual(read_dataset({}, "agent-scopes"), fallback.return_value)
        fallback.assert_called_once_with(connection)
        for dataset, status, detail in [
            ("resources", 404, "O conjunto de dados não existe."),
            ("agent-scopes", 403, "Forbidden"),
            ("agent-scopes", 503, "Unavailable"),
            ("agent-scopes", 404, "Not Found"),
        ]:
            with self.subTest(dataset=dataset, status=status, detail=detail):
                fallback.reset_mock()
                response.status_code = status
                response.json.return_value = {"detail": detail}
                with self.assertRaises(SolidSETDataAPIError):
                    read_dataset({}, dataset)
                fallback.assert_not_called()

    def test_legacy_scope_pages_are_parameterized_and_complete(self):
        connection = MagicMock(max_rows=2)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"total": 3}, {"total": 3}]
        cursor.fetchall.side_effect = [[{"id": 1}, {"id": 2}], [{"id": 3}]]
        self.assertEqual(_read_legacy_agent_scopes(connection), [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (0, 2))
        self.assertEqual(cursor.execute.call_args_list[2].args[1], (2, 1))

    def test_legacy_scope_partial_read_is_rejected(self):
        connection = MagicMock(max_rows=2)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"total": 2}
        cursor.fetchall.return_value = [{"id": 1}]
        with self.assertRaisesRegex(SolidSETDataAPIError, "incompleta"):
            _read_legacy_agent_scopes(connection)

    def test_legacy_scope_changed_count_is_rejected(self):
        connection = MagicMock(max_rows=2)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"total": 1}, {"total": 2}]
        cursor.fetchall.return_value = [{"id": 1}]
        with self.assertRaisesRegex(SolidSETDataAPIError, "mudaram"):
            _read_legacy_agent_scopes(connection)

    def test_legacy_query_matches_data_api_definition(self):
        import runpy
        from pathlib import Path
        from app.connectors.agent_scope_query import AGENT_SCOPES_QUERY
        path = Path(__file__).resolve().parents[2] / "solidset-data-api" / "app" / "queries.py"
        self.assertEqual(AGENT_SCOPES_QUERY.strip(), runpy.run_path(str(path))["DATASETS"]["agent-scopes"].strip())

    def test_legacy_sql_comments_are_removed_before_gateway(self):
        query = """SELECT TOP 1 ID -- legacy note
        FROM dbo.SysChat /* read only */
        WHERE ID = %s"""
        clean = _strip_sql_comments(query)
        self.assertNotIn("--", clean)
        self.assertNotIn("/*", clean)
        self.assertIn("FROM dbo.SysChat", clean)

    @patch("app.connectors.solidset_data_api.decrypt_api_key", return_value="secret")
    @patch("app.connectors.solidset_data_api.httpx.Client")
    def test_dataset_reads_all_pages(self, client_type, _decrypt):
        first = Mock(status_code=200)
        first.json.return_value = {
            "rows": [{"IDWorkRoom": "1"}],
            "hasMore": True,
            "nextOffset": 1,
        }
        second = Mock(status_code=200)
        second.json.return_value = {
            "rows": [{"IDWorkRoom": "2"}],
            "hasMore": False,
            "nextOffset": None,
        }
        client_type.return_value.get.side_effect = [first, second]

        rows = read_dataset({
            "BaseUrl": "https://data.example.test",
            "EncryptedAPIKey": "encrypted",
            "MaxRows": 500,
        }, "workrooms")

        self.assertEqual([{"IDWorkRoom": "1"}, {"IDWorkRoom": "2"}], rows)
        self.assertEqual(
            {"offset": 1, "limit": 500},
            client_type.return_value.get.call_args_list[1].kwargs["params"],
        )

    @patch("app.connectors.solidset_data_api.os.path.exists", return_value=True)
    def test_localhost_uses_host_gateway_inside_docker(self, _exists):
        self.assertEqual(
            "http://host.docker.internal:8080",
            _runtime_base_url("http://localhost:8080/"),
        )

    @patch("app.connectors.solidset_data_api.os.path.exists", return_value=False)
    @patch.dict("app.connectors.solidset_data_api.os.environ", {}, clear=True)
    def test_localhost_is_preserved_outside_docker(self, _exists):
        self.assertEqual(
            "http://localhost:8080",
            _runtime_base_url("http://localhost:8080/"),
        )

    def test_direct_sql_fallback_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "acesso SQL Server direto está desativado"):
            with connect_solidset_data({"Database": {"active": True}}, as_dict=True):
                pass

    @patch("app.connectors.solidset_data_api.decrypt_api_key", return_value="secret")
    @patch("app.connectors.solidset_data_api.httpx.Client")
    def test_cursor_preserves_pymssql_dict_contract(self, client_type, _decrypt):
        response = Mock(status_code=200)
        response.json.return_value = {
            "columns": ["IDChat2", "RawMessage"],
            "rows": [{"IDChat2": 10, "RawMessage": "Olá"}],
            "rowCount": 1,
        }
        client_type.return_value.post.return_value = response
        connection = DataAPIConnection({
            "BaseUrl": "https://data.example.test",
            "EncryptedAPIKey": "encrypted",
            "MaxRows": 500,
            "VerifyTLS": True,
        }, as_dict=True)

        cursor = connection.cursor(as_dict=True)
        cursor.execute("SELECT IDChat2, RawMessage FROM dbo.SysChat WHERE IDChat2>%s", (9,))

        self.assertEqual({"IDChat2": 10, "RawMessage": "Olá"}, cursor.fetchone())
        sent = client_type.return_value.post.call_args.kwargs["json"]
        self.assertEqual([9], sent["parameters"])
        self.assertEqual(500, sent["maxRows"])

    @patch("app.connectors.solidset_data_api.decrypt_api_key", return_value="secret")
    @patch("app.connectors.solidset_data_api.httpx.Client")
    def test_reads_filtered_structured_schema_catalog(self, client_type, _decrypt):
        response = Mock(status_code=200)
        response.json.return_value = {
            "databaseName": "ISIFrameIsicom",
            "tables": [{
                "schemaName": "dbo",
                "tableName": "SysMeeting",
                "columns": [{"name": "ID", "dataType": "uniqueidentifier"}],
                "foreignKeys": [],
            }],
        }
        client_type.return_value.get.return_value = response

        catalog = read_schema_catalog({
            "BaseUrl": "https://data.example.test",
            "EncryptedAPIKey": "encrypted",
        }, ["SysMeeting", "SysMeeting2Resource"])

        self.assertEqual("ISIFrameIsicom", catalog["databaseName"])
        self.assertEqual("SysMeeting", catalog["tables"][0]["tableName"])
        self.assertEqual(
            {"tables": "SysMeeting,SysMeeting2Resource"},
            client_type.return_value.get.call_args.kwargs["params"],
        )


if __name__ == "__main__":
    unittest.main()
