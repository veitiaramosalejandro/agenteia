import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.controllers import llm_configuration as controller
from app.api.schemas.llm_configuration import LLMProviderConfiguration
from app.connectors.db_client import save_llm_provider_configuration


class ProviderFromEnvTests(unittest.TestCase):
    def test_provider_creation_rejects_resource_association(self):
        r = self.client.post("/api/v1/agent/llm/providers/from-env", json={
            "Code": "resource-local", "Name": "Local", "IDResource": str(uuid4()),
        })
        self.assertEqual(r.status_code, 422)
        r = self.client.put("/api/v1/agent/llm/providers/resource-local", json={
            "Code": "resource-local", "Name": "Local", "Provider": "ollama",
            "Model": "test", "IDResource": str(uuid4()),
        })
        self.assertEqual(r.status_code, 422)

    def setUp(self):
        app = FastAPI()
        app.include_router(controller.router)
        self.client = TestClient(app)

    def saved(self, payload, **kwargs):
        return {**payload, "ID": uuid4(), "HasAPIKey": bool(payload.get("APIKey")),
                "CreatedAt": datetime.now(timezone.utc), "UpdatedAt": datetime.now(timezone.utc)}

    @patch.object(controller, "create_chat_model")
    @patch.object(controller, "save_llm_provider_configuration")
    def test_openai_defaults_are_copied_and_secret_is_not_returned(self, save, model):
        save.side_effect = self.saved
        with patch.object(controller.settings, "OPENAI_API_KEY", "secret-example"):
            r = self.client.post("/api/v1/agent/llm/providers/from-env", json={
                "Source": "openai_search", "Code": "test-search", "Name": "Búsqueda"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertNotIn("secret-example", r.text)
        self.assertNotIn("APIKey", r.json()["configuration"])
        self.assertTrue(r.json()["configuration"]["HasAPIKey"])
        payload = save.call_args.args[0]
        self.assertEqual(payload["Model"], controller.settings.OPENAI_SEARCH_MODEL)
        self.assertEqual(payload["APIKey"], "secret-example")
        self.assertFalse(payload["IsDefault"])
        self.assertFalse(payload["StoreResponses"])
        self.assertTrue(save.call_args.kwargs["create_only"])

    @patch.object(controller, "create_chat_model")
    @patch.object(controller, "save_llm_provider_configuration")
    def test_runtime_ollama_does_not_copy_openai_secret(self, save, model):
        save.side_effect = self.saved
        with patch.object(controller.settings, "LLM_PROVIDER", "ollama"), patch.object(
            controller.settings, "LLM_API_KEY", ""
        ), patch.object(controller.settings, "OPENAI_API_KEY", "private"):
            r = self.client.post("/api/v1/agent/llm/providers/from-env", json={"Code": "local", "Name": "Local"})
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(save.call_args.args[0]["APIKey"])

    @patch.object(controller, "save_llm_provider_configuration")
    def test_missing_key_does_not_write(self, save):
        with patch.object(controller.settings, "OPENAI_API_KEY", ""):
            r = self.client.post("/api/v1/agent/llm/providers/from-env", json={"Source": "openai_search", "Code": "search", "Name": "Search"})
        self.assertEqual(r.status_code, 422)
        save.assert_not_called()

    @patch.object(controller, "create_chat_model")
    @patch.object(controller, "save_llm_provider_configuration", side_effect=FileExistsError())
    def test_duplicate_returns_conflict(self, save, model):
        with patch.object(controller.settings, "LLM_PROVIDER", "ollama"):
            r = self.client.post("/api/v1/agent/llm/providers/from-env", json={"Code": "existing", "Name": "Existing"})
        self.assertEqual(r.status_code, 409)

    def test_unknown_fields_and_blank_name_are_rejected(self):
        for body in [{"Code": "x", "Name": " "}, {"Code": "x", "Name": "X", "APIKey": "not-allowed"}]:
            self.assertEqual(self.client.post("/api/v1/agent/llm/providers/from-env", json=body).status_code, 422)

    def test_all_manual_fields_have_explanations(self):
        self.assertTrue(all(f.description for f in LLMProviderConfiguration.model_fields.values()))

    @patch("app.connectors.db_client.ensure_llm_provider_schema")
    @patch("app.connectors.db_client._postgres_connection")
    def test_duplicate_is_checked_before_changing_defaults(self, connection, schema):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"exists": 1}
        with self.assertRaises(FileExistsError):
            save_llm_provider_configuration({"Code": "existing", "IsDefault": True}, create_only=True)
        self.assertEqual(cursor.execute.call_count, 2)
        for call in cursor.execute.call_args_list:
            self.assertTrue(call.args[0].startswith("SELECT"))
