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
            self.assertEqual(self.client.post('/api/v1/agent/llm/nvidia/test', json={'stream': False}).status_code, 503)
        for body in ({"prompt": " "}, {"max_tokens": 16385}, {"base_url": "http://localhost"},
                     {"timeout_seconds": 9}, {"timeout_seconds": 601}):
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
                self.assertEqual(payload['reasoning_effort'], 'max')
                self.assertEqual(payload['seed'], 0)
                self.assertNotIn('chat_template_kwargs', payload)
                if status != 200:
                    return httpx.Response(status, json={"error": {"message": "secret-upstream", "type": "error"}})
                return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
                    "model": NVIDIA_MODEL, "choices": [{"index": 0, "message": {
                        "role": "assistant", "content": "GPU", "reasoning_content": "private reasoning"},
                        "finish_reason": "stop"}]})

            def factory(**kwargs):
                return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

            with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI', side_effect=factory):
                response = self.client.post('/api/v1/agent/llm/nvidia/test', json={'stream': False})
            self.assertEqual(response.status_code, expected, response.text)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('secret-upstream', response.text)
            self.assertNotIn('private reasoning', response.text)

    def test_provider_uses_chat_completions_payload(self):
        model = create_chat_model(LLMProviderConfig(provider='nvidia', model=NVIDIA_MODEL, api_key='test-only'))
        payload = model._get_request_payload([('human', 'Hola')])
        self.assertFalse(model.use_responses_api)
        self.assertEqual(payload['reasoning_effort'], 'max')
        self.assertNotIn('top_p', payload)
        self.assertNotIn('service_tier', payload)
        self.assertNotIn('store', payload)

    def test_registered_models_send_selected_model_to_nvidia(self):
        import json
        from app.llm.providers import provider_config_from_record
        observed = []
        def handler(request):
            self.assertEqual(str(request.url), 'https://integrate.api.nvidia.com/v1/chat/completions')
            payload = json.loads(request.content)
            observed.append(payload['model'])
            return httpx.Response(200, json={'id': 'test', 'created': 1,
                'object': 'chat.completion', 'model': payload['model'],
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'},
                             'finish_reason': 'stop'}]})
        names = ['moonshotai/kimi-k3', 'nvidia/nemotron-3-ultra-550b-a55b', 'another/model']
        for name in names:
            config = provider_config_from_record({'Provider': 'nvidia', 'Model': name, 'APIKey': 'test-only'})
            model = create_chat_model(config)
            from openai import OpenAI
            with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
                with OpenAI(api_key='test-only', base_url=model.openai_api_base,
                            http_client=http_client, max_retries=0) as sdk:
                    model.client = sdk.chat.completions
                    model.root_client = sdk
                    self.assertEqual(model.invoke('Hola').content, 'OK')
        self.assertEqual(observed, names)

    def test_timeout_returns_504(self):
        with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI') as factory:
            client = factory.return_value.__aenter__.return_value
            client.chat.completions.create = AsyncMock(side_effect=TimeoutError())
            response = self.client.post('/api/v1/agent/llm/nvidia/test', json={'stream': False})
        self.assertEqual(response.status_code, 504)

    def test_image_stream_and_upstream_cleanup(self):
        import json
        clients = []

        def handler(request):
            payload = json.loads(request.content)
            self.assertTrue(payload['stream'])
            self.assertEqual(payload['messages'][0]['content'], [
                {'type': 'text', 'text': 'What is in this image?'},
                {'type': 'image_url', 'image_url': {'url': 'https://example.com/image.jpg'}}])
            chunk = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1,
                     'model': NVIDIA_MODEL, 'choices': [{'index': 0,
                     'delta': {'content': 'An image'}, 'finish_reason': None}]}
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')

        def factory(**kwargs):
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            clients.append(client)
            return AsyncOpenAI(**kwargs, http_client=client)

        with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI', side_effect=factory):
            response = self.client.post('/api/v1/agent/llm/nvidia/test', json={
                'prompt': 'What is in this image?', 'image_url': 'https://example.com/image.jpg'})
        self.assertIn('text/event-stream', response.headers['content-type'])
        self.assertIn('An image', response.text)
        self.assertTrue(response.text.endswith('data: [DONE]\n\n'))
        self.assertTrue(clients[0].is_closed)

    def test_stream_failure_is_sanitized_event(self):
        with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI') as factory:
            client = factory.return_value.__aenter__.return_value
            client.chat.completions.create = AsyncMock(side_effect=TimeoutError('secret'))
            response = self.client.post('/api/v1/agent/llm/nvidia/test', json={'stream': True})
        self.assertIn('event: error', response.text)
        self.assertIn('504', response.text)
        self.assertNotIn('secret', response.text)
        self.assertNotIn('[DONE]', response.text)

    def test_timeout_override_applies_to_sdk_but_not_nvidia_payload(self):
        for streaming in (False, True):
            with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI') as factory:
                client = factory.return_value.__aenter__.return_value
                client.chat.completions.create = AsyncMock(side_effect=TimeoutError())
                response = self.client.post('/api/v1/agent/llm/nvidia/test', json={
                    'stream': streaming, 'timeout_seconds': 300})
            self.assertEqual(factory.call_args.kwargs['timeout'].read, 300)
            self.assertEqual(factory.call_args.kwargs['timeout'].connect, 10)
            self.assertEqual(factory.call_args.kwargs['max_retries'], 0)
            self.assertNotIn('timeout_seconds', client.chat.completions.create.call_args.kwargs)
            self.assertIn('request_deadline_exceeded', response.text)
            self.assertIn('300', response.text)

    def test_sdk_timeout_is_distinct_from_local_deadline(self):
        from openai import APITimeoutError
        with patch.object(nvidia.settings, 'NVIDIA_API_KEY', 'test-only'), patch.object(nvidia, 'AsyncOpenAI') as factory:
            client = factory.return_value.__aenter__.return_value
            client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(
                request=httpx.Request('POST', 'https://integrate.api.nvidia.com/v1/chat/completions')))
            response = self.client.post('/api/v1/agent/llm/nvidia/test', json={'stream': True})
        self.assertIn('upstream_timeout', response.text)
        self.assertIn('first_chunk', response.text)
        self.assertNotIn('request_deadline_exceeded', response.text)

    def test_real_deadline_cancels_slow_stream_and_closes_resources(self):
        import asyncio
        from types import SimpleNamespace
        closed = []
        class SlowStream:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                closed.append('stream')
            async def __aiter__(self):
                yield SimpleNamespace(model_dump_json=lambda **kwargs: '{"choices":[]}')
                await asyncio.sleep(1)
        class Client:
            chat = SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=SlowStream())))
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                closed.append('client')
        async def collect():
            return [event async for event in nvidia._stream({'stream': True}, timeout_seconds=0.01)]
        with patch.object(nvidia, '_client', return_value=Client()):
            events = asyncio.run(collect())
        self.assertEqual(closed, ['stream', 'client'])
        self.assertIn('"choices":[]', events[0])
        self.assertIn('request_deadline_exceeded', events[-1])
        self.assertIn('"phase": "stream"', events[-1])
        self.assertNotIn('[DONE]', ''.join(events))


if __name__ == '__main__':
    unittest.main()
