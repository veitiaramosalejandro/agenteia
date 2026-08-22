import unittest
from unittest.mock import Mock, patch

from app.connectors.solidset_data_api import DataAPIConnection
from app.connectors.solidset_sql import connect as connect_solidset_data


class SolidSETDataAPIConnectorTests(unittest.TestCase):
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
