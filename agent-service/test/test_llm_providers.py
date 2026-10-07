import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_openai_uses_responses_api_without_remote_storage(self):
        captured = {}

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch("app.llm.providers._optional_class", return_value=FakeChatOpenAI):
            create_chat_model(LLMProviderConfig(
                provider="openai",
                model="company-approved-model",
                api_key="test-only",
                project="proj_test",
                organization="org_test",
                max_output_tokens=384,
                timeout_seconds=30,
                max_retries=2,
            ))

        self.assertTrue(captured["use_responses_api"])
        self.assertFalse(captured["store"])
        self.assertEqual(captured["max_completion_tokens"], 384)
        self.assertEqual(captured["max_retries"], 2)
        self.assertEqual(captured["organization"], "org_test")
        self.assertEqual(captured["default_headers"]["OpenAI-Project"], "proj_test")

    def test_standard_openai_key_environment_is_supported(self):
        config = provider_config_from_settings(SimpleNamespace(
            LLM_PROVIDER="openai", LLM_BASE_URL="", MODEL_NAME="approved-model",
            LLM_API_KEY="", OPENAI_API_KEY="secret-from-environment",
            LLM_TEMPERATURE=0.2, LLM_MAX_OUTPUT_TOKENS=500,
            LLM_REQUEST_TIMEOUT_SECONDS=30,
        ))
        self.assertEqual(config.api_key, "secret-from-environment")
        self.assertTrue(config.use_responses_api)
        self.assertFalse(config.store_responses)


if __name__ == "__main__":
    unittest.main()
