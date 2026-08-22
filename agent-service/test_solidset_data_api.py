import unittest
from unittest.mock import Mock, patch

from app.connectors.solidset_data_api import (
    DataAPIConnection,
    _runtime_base_url,
    read_dataset,
)
from app.connectors.solidset_sql import connect as connect_solidset_data


class SolidSETDataAPIConnectorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
