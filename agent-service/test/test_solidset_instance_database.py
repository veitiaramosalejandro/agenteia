import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.main import SolidSETInstanceConfiguration
from app.connectors.solidset_sql import (
    connection_options,
    decrypt_sql_password,
    encrypt_sql_password,
)


class SolidSETInstanceDatabaseTests(unittest.TestCase):
    def test_instance_contract_accepts_data_api(self):
        configuration = SolidSETInstanceConfiguration(
            Code="solidset-lisboa",
            Name="SolidSET Lisboa",
            BaseUrl="https://solidset.example:52130",
            DataAPI={
                "BaseUrl": "https://solidset.example:8081",
                "APIKey": "secret",
            },
        )
        self.assertEqual(
            "https://solidset.example:8081",
            configuration.DataAPI.BaseUrl,
        )

    def test_instance_contract_rejects_legacy_database(self):
        with self.assertRaises(ValidationError):
            SolidSETInstanceConfiguration(
                Code="solidset-lisboa",
                Name="SolidSET Lisboa",
                BaseUrl="https://solidset.example:52130",
                Database={
                    "Host": "sql.example",
                    "DatabaseName": "SolidSET",
                    "Username": "sa",
                    "Password": "secret",
                },
            )

    def test_named_instance_does_not_force_port(self):
        options = connection_options({"Database": {
            "Host": "host.docker.internal", "InstanceName": "SQL2017DEV", "Port": 1433,
        }})
        self.assertEqual("host.docker.internal\\SQL2017DEV", options["server"])
        self.assertNotIn("port", options)

    def test_direct_instance_uses_configured_port(self):
        options = connection_options({"Database": {
            "Host": "10.0.0.20", "InstanceName": None, "Port": 57258,
        }})
        self.assertEqual({"server": "10.0.0.20", "port": 57258}, options)

    def test_docker_translates_localhost_to_host_gateway(self):
        with patch("app.connectors.solidset_sql.os.path.exists", return_value=True):
            options = connection_options({"Database": {
                "Host": "localhost", "InstanceName": None, "Port": 1433,
            }})
        self.assertEqual("host.docker.internal", options["server"])

    def test_password_is_encrypted_at_rest(self):
        key = Fernet.generate_key().decode()
        with patch("app.llm.secrets.settings.LLM_CREDENTIAL_ENCRYPTION_KEY", key):
            stored = encrypt_sql_password("secret-value")
            self.assertNotIn("secret-value", stored)
            self.assertEqual("secret-value", decrypt_sql_password(stored))

    def test_invalid_persisted_key_is_preserved_and_regenerated(self):
        from app.llm.secrets import _credential_key

        with TemporaryDirectory() as directory:
            key_path = Path(directory) / "credential.key"
            key_path.write_text("not-a-key", encoding="ascii")
            with (
                patch("app.llm.secrets.settings.LLM_CREDENTIAL_ENCRYPTION_KEY", ""),
                patch("app.llm.secrets.settings.CREDENTIAL_ENCRYPTION_KEY_FILE", str(key_path)),
            ):
                generated = _credential_key()
            self.assertEqual(44, len(generated))
            self.assertTrue(list(Path(directory).glob("credential.key.invalid-*")))


if __name__ == "__main__":
    unittest.main()
