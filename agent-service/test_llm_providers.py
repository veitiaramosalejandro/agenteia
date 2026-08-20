import unittest
from types import SimpleNamespace

from app.llm.providers import (
    ChatProvider,
    LLMProviderConfig,
    ProviderRegistry,
    create_chat_model,
    provider_config_from_settings,
    provider_config_from_record,
)


class _FakeProvider(ChatProvider):
    name = "test_provider"

    def create_model(self, config):
        return {"provider": config.provider, "model": config.model}


class LLMProviderTests(unittest.TestCase):
    def test_registry_can_add_provider_without_changing_agent(self):
        ProviderRegistry.register("test-provider", _FakeProvider)
        model = create_chat_model(LLMProviderConfig(
            provider="test-provider",
            model="specialized-model",
        ))
        self.assertEqual(model["model"], "specialized-model")

    def test_ollama_uses_legacy_base_url_by_default(self):
        config = provider_config_from_settings(SimpleNamespace(
            LLM_PROVIDER="ollama",
            LLM_BASE_URL="",
            OLLAMA_BASE_URL="http://ollama:11434",
            MODEL_NAME="qwen2.5:3b",
            LLM_API_KEY="",
            LLM_TEMPERATURE=0.3,
            LLM_MAX_OUTPUT_TOKENS=500,
            LLM_REQUEST_TIMEOUT_SECONDS=90,
            AZURE_OPENAI_ENDPOINT="",
            AZURE_OPENAI_API_VERSION="",
            AZURE_OPENAI_DEPLOYMENT="",
        ))
        self.assertEqual(config.base_url, "http://ollama:11434")
        self.assertEqual(config.provider, "ollama")

    def test_unknown_provider_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Proveedor LLM desconocido"):
            create_chat_model(LLMProviderConfig(provider="missing", model="x"))

    def test_postgres_record_maps_to_common_contract(self):
        config = provider_config_from_record({
            "Provider": "anthropic", "Model": "claude-test", "APIKey": "secret",
            "Temperature": 0.2, "MaxOutputTokens": 700, "TimeoutSeconds": 45,
        })
        self.assertEqual(config.provider, "anthropic")
        self.assertEqual(config.api_key, "secret")
        self.assertEqual(config.max_output_tokens, 700)


if __name__ == "__main__":
    unittest.main()
