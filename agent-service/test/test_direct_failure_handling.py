import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch, AsyncMock

from app.services import openai_direct, response_status, auto_reply


class DirectFailureTests(unittest.TestCase):
    def test_attribute_error_retries_once_with_fresh_client_and_same_configuration(self):
        first, second = Mock(), Mock()
        first.invoke.side_effect = AttributeError('sensitive provider body')
        second.invoke.return_value = 'answer'
        record = {'Model': 'assigned-model'}
        with patch.object(openai_direct, 'create_chat_model', side_effect=[first, second]) as factory, \
                patch.object(openai_direct, 'provider_config_from_record', return_value='same-config'), \
                redirect_stdout(io.StringIO()):
            result = openai_direct._invoke_direct_model(record, ['question'], {'chat_id': 'r'})
        self.assertEqual(result, 'answer')
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(all(call.args == ('same-config',) for call in factory.call_args_list))
        self.assertEqual(first.invoke.call_args, second.invoke.call_args)

    def test_persistent_attribute_error_stops_and_logs_only_safe_diagnostics(self):
        model = Mock()
        model.invoke.side_effect = AttributeError('secret-key and private prompt')
        output = io.StringIO()
        with patch.object(openai_direct, 'create_chat_model', return_value=model) as factory, \
                patch.object(openai_direct, 'provider_config_from_record'), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'AttributeError; referencia') as caught:
                openai_direct._invoke_direct_model({'Model': 'test'}, [], {'chat_id': 'r', 'agent_resource_id': 'a'})
        self.assertEqual(factory.call_count, 2)
        self.assertNotIn('secret-key', output.getvalue() + str(caught.exception))
        self.assertNotIn('private prompt', output.getvalue() + str(caught.exception))
        events = [json.loads(line.split(' ', 1)[1]) for line in output.getvalue().splitlines()]
        self.assertEqual(events[0]['incident_id'], events[1]['incident_id'])
        self.assertEqual(events[0]['request_id'], 'r')
        self.assertEqual(events[0]['agent'], 'a')
        self.assertTrue(events[0]['frames'])

    def test_timeout_is_not_retried_beyond_provider_policy(self):
        model = Mock()
        model.invoke.side_effect = TimeoutError('private request')
        with patch.object(openai_direct, 'create_chat_model', return_value=model) as factory, \
                patch.object(openai_direct, 'provider_config_from_record'), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'TimeoutError'):
                openai_direct._invoke_direct_model({'Model': 'test'}, [], {})
        self.assertEqual(factory.call_count, 1)


class GroupStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = {}
        redis_mock = Mock()
        redis_mock.get.side_effect = self.storage.get
        redis_mock.setex.side_effect = lambda key, ttl, value: self.storage.__setitem__(key, value)
        mock = patch.object(response_status, '_redis', redis_mock)
        mock.start()
        self.addCleanup(mock.stop)
        response_status.create('r', 'chat', 2)
        for agent in ('a', 'b'):
            response_status.update('r', 'queued', agent_resource_id=agent)

    def test_success_does_not_complete_other_agent(self):
        response_status.update('r', 'processing', agent_resource_id='b')
        response_status.update('r', 'completed', agent_resource_id='a', response_count=1)
        state = response_status.load('r')
        self.assertFalse(state['completed'])
        self.assertEqual(state['responseCount'], 1)
        response_status.update('r', 'failed', agent_resource_id='b', error='Model failed')
        response_status.update('r', 'completed', response_count=2)
        state = response_status.load('r')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['responseCount'], 1)
        self.assertEqual([a['status'] for a in state['agents']], ['completed', 'failed'])

    def test_queued_delivery_does_not_count_as_sent(self):
        response_status.update('r', 'completed', agent_resource_id='a')
        response_status.update('r', 'queued', agent_resource_id='b')
        response_status.update('r', 'completed', response_count=2)
        state = response_status.load('r')
        self.assertEqual(state['status'], 'queued')
        self.assertFalse(state['completed'])
        self.assertEqual(state['responseCount'], 1)

    def test_both_successful_responses_complete_request(self):
        for agent in ('a', 'b'):
            response_status.update('r', 'completed', agent_resource_id=agent)
        state = response_status.load('r')
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['responseCount'], 2)

    async def test_parallel_failure_is_assigned_to_failed_agent_without_replaying_success(self):
        candidates = [
            {'fingerprint': 'a', 'agent_resource_id': 'a', 'response_request_id': 'r'},
            {'fingerprint': 'b', 'agent_resource_id': 'b', 'response_request_id': 'r'},
        ]
        async def child(items, **kwargs):
            agent = items[0]['agent_resource_id']
            if agent == 'b':
                raise RuntimeError('private exception details')
            response_status.update('r', 'completed', agent_resource_id=agent)
            return 1
        original = auto_reply._process_auto_replies
        with patch.object(auto_reply, '_route_candidates_to_selected_agents', return_value=candidates), \
                patch.object(auto_reply, '_process_auto_replies', new=AsyncMock(side_effect=child)) as child_mock, \
                patch.object(auto_reply.settings, 'SOLIDSET_AUTO_REPLY_ENABLED', True), \
                patch.object(auto_reply.settings, 'SOLIDSET_USER_ACTIONS_ENABLED', True), \
                redirect_stdout(io.StringIO()):
            accepted = await original(candidates)
        self.assertEqual(accepted, 1)
        self.assertEqual(child_mock.await_count, 2)
        state = response_status.load('r')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['responseCount'], 1)
        self.assertEqual(state['agents'][1]['status'], 'failed')
        self.assertNotIn('private exception details', json.dumps(state))


if __name__ == '__main__':
    unittest.main()
