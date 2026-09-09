import unittest
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.api.controllers import nvidia
from app.llm.providers import NVIDIA_MODEL, LLMProviderConfig, create_chat_model


class NvidiaTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(nvidia.router)
        self.client = TestClient(app)

    def test_missing_key_and_invalid_input(self):
        with patch.object(nvidia.settings, "NVIDIA_API_KEY", ""):
            self.assertEqual(self.client.post('/api/v1/agent/llm/nvidia/test', json={}).status_code, 503)
        for body in ({"prompt": " "}, {"max_tokens": 16385}, {"base_url": "http://localhost"}):
            self.assertEqual(self.client.post('/api/v1/agent/llm/nvidia/test', json=body).status_code, 422)

    def test_actual_sdk_serialization_and_safe_errors(self):
        import json
        for status, expected in ((200, 200), (401, 502), (429, 429), (500, 502)):
            calls = []

            def handler(request):
                calls.append(request)
                self.assertEqual(str(request.url), 'https://integrate.api.nvidia.com/v1/chat/completions')
                payload = json.loads(request.content)
                self.assertEqual(payload['model'], NVIDIA_MODEL)
                self.assertEqual(payload['max_tokens'], 256)
                self.assertFalse(payload['chat_template_kwargs']['enable_thinking'])
                if status != 200:
                    return httpx.Response(status, json={"error": {"message": "secret-upstream", "type": "error"}})
                return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
                    "model": NVIDIA_MODEL, "choices": [{"index": 0, "message": {
                        "role": "assistant", "content": "GPU", "reasoning_content": "private reasoning"},
                        "finish_reason": "stop"}]})

            def factory(**kwargs):
                return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

            with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI', side_effect=factory):
                response = self.client.post('/api/v1/agent/llm/nvidia/test', json={})
            self.assertEqual(response.status_code, expected, response.text)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('secret-upstream', response.text)
            self.assertNotIn('private reasoning', response.text)

    def test_provider_uses_chat_completions_payload(self):
        model = create_chat_model(LLMProviderConfig(provider='nvidia', model=NVIDIA_MODEL, api_key='test-only'))
        payload = model._get_request_payload([('human', 'Hola')])
        self.assertFalse(model.use_responses_api)
        self.assertEqual(payload['extra_body']['chat_template_kwargs'], {'enable_thinking': True})
        self.assertNotIn('service_tier', payload)
        self.assertNotIn('store', payload)

    def test_timeout_returns_504(self):
        with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI') as factory:
            client = factory.return_value.__aenter__.return_value
            client.chat.completions.create = AsyncMock(side_effect=TimeoutError())
            response = self.client.post('/api/v1/agent/llm/nvidia/test', json={})
        self.assertEqual(response.status_code, 504)


if __name__ == '__main__':
    unittest.main()
