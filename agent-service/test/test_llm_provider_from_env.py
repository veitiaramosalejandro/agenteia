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
    @patch.object(controller, "save_llm_provider_configuration")
    def test_register_multiple_nvidia_models_and_build_saved_configs(self, save):
        from app.llm.providers import create_chat_model, provider_config_from_record
        save.side_effect = self.saved
        runtime = MagicMock()
        self.client.app.state.agent = runtime
        models = ['moonshotai/kimi-k3', 'nvidia/nemotron-3-ultra-550b-a55b', 'another/model']
        with patch.object(controller.settings, 'NVIDIA_API_KEY', 'test-only'):
            for index, name in enumerate(models):
                response = self.client.post('/api/v1/agent/llm/providers/from-env', json={
                    'Source': 'nvidia', 'Code': f'nvidia-{index}', 'Name': name, 'Model': name})
                self.assertEqual(response.status_code, 201, response.text)
                record = save.call_args.args[0]
                self.assertEqual(response.json()['configuration']['Model'], name)
                model = create_chat_model(provider_config_from_record(record))
                payload = model._get_request_payload([('human', 'Hola')])
                self.assertEqual(payload['model'], name)
                self.assertEqual(str(model.openai_api_base), 'https://integrate.api.nvidia.com/v1')
                if name == 'another/model':
                    self.assertNotIn('extra_body', payload)
                    self.assertNotIn('reasoning_effort', payload)
        self.assertEqual(save.call_count, 3)
        self.assertEqual(runtime.clear_llm_configuration_cache.call_count, 3)

    def test_nvidia_registration_requires_explicit_model(self):
        for model in (None, '', ' ', 'model with spaces'):
            response = self.client.post('/api/v1/agent/llm/providers/from-env', json={
                'Source': 'nvidia', 'Code': 'nvidia', 'Name': 'NVIDIA', 'Model': model})
            self.assertEqual(response.status_code, 422)

    @patch.object(controller, 'save_agent_model_configuration')
    def test_change_agent_nvidia_assignment_clears_cache(self, save):
        resource = uuid4()
        runtime = MagicMock()
        self.client.app.state.agent = runtime
        def saved_assignment(resource_id, data):
            return {**data, 'ID': uuid4(), 'IDResource': resource_id,
                    'IDProviderConfiguration': uuid4(),
                    'CreatedAt': datetime.now(timezone.utc), 'UpdatedAt': datetime.now(timezone.utc)}
        save.side_effect = saved_assignment
        for code in ('nvidia-kimi', 'nvidia-nemotron'):
            response = self.client.put(f'/api/v1/agent/solidset/agents/{resource}/model', json={
                'ProviderCode': code, 'LocalExecution': False, 'IsDefault': True,
                'Capabilities': ['general'], 'Priority': 0})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['ProviderCode'], code)
            self.assertEqual(save.call_args.args[0], resource)
        self.assertEqual(runtime.clear_llm_configuration_cache.call_count, 2)

    @patch.object(controller, "create_chat_model")
    @patch.object(controller, "save_llm_provider_configuration")
    def test_nvidia_source_uses_own_key_and_no_resource(self, save, model):
        save.side_effect = self.saved
        with patch.object(controller.settings, "NVIDIA_API_KEY", "nvidia-test-only"):
            response = self.client.post("/api/v1/agent/llm/providers/from-env", json={
                "Source": "nvidia", "Code": "nvidia", "Name": "NVIDIA", "Model": "moonshotai/kimi-k3"})
        self.assertEqual(response.status_code, 201, response.text)
        payload = save.call_args.args[0]
        self.assertEqual(payload['Provider'], 'nvidia')
        self.assertEqual(payload['APIKey'], 'nvidia-test-only')
        self.assertFalse(payload['UseResponsesAPI'])
        self.assertNotIn('IDResource', payload)
        self.assertNotIn('nvidia-test-only', response.text)

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
