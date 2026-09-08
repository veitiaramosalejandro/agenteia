import json
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from langchain_core.messages import AIMessage

from app.api.controllers import agent_management as controller
from app.api.schemas.common import MultiAgentDialogueRequest
from app.services import openai_direct


class TwinPromptTests(unittest.TestCase):
    def test_selected_identity_reaches_model_and_bypasses_shared_cache(self):
        for owner, sender in (('owner-a', 'owner-a'), ('owner-b', 'owner-a')):
            metadata = {
                'source': 'solidset_multi_agent', 'agent_resource_id': owner,
                'agent_identity_id': 'twin-' + owner, 'sender_resource_id': sender,
                'agent_name': 'Name ' + owner, 'workroom_id': 'room',
                'agent_knowledge': 'Private ' + owner,
                'agent_profile': {'FullName': 'Owner ' + owner, 'APIKey': 'secret'},
                'agent_system_prompt': 'Explain clearly',
            }
            with self.subTest(owner=owner), \
                    patch.object(openai_direct, 'assigned_openai', return_value={'Model': 'test'}), \
                    patch.object(openai_direct, '_learned_answer') as learned, \
                    patch.object(openai_direct, 'enqueue_learning') as enqueue, \
                    patch.object(openai_direct, '_invoke_direct_model', return_value=AIMessage(content='Reply')) as invoke:
                self.assertEqual(openai_direct.answer_direct('Quem és?', metadata, 'session'), 'Reply')
                messages = invoke.call_args.args[1]
                context = json.loads(messages[-2].content.split('\n', 1)[1])
                self.assertEqual(context['selected_twin_name'], 'Name ' + owner)
                self.assertEqual(context['represented_human_resource'], owner)
                self.assertEqual(context['sender_is_represented_human'], owner == sender)
                self.assertEqual(context['knowledge'], 'Private ' + owner)
                self.assertNotIn('secret', messages[-2].content)
                self.assertEqual(messages[-1].content, 'Quem és?')
                learned.assert_not_called()
                enqueue.assert_not_called()


class TwinEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_dialogue_loads_instance_profile_and_keeps_twins_separate(self):
        room, sender, other, instance_id = uuid4(), uuid4(), uuid4(), uuid4()
        configured = [
            {'IDResource': sender, 'IDAgentResource': uuid4(), 'Name': 'First'},
            {'IDResource': other, 'IDAgentResource': uuid4(), 'Name': 'Second'},
        ]
        instance = {'ID': instance_id, 'Code': 'local', 'Locale': 'pt-PT'}
        request = MultiAgentDialogueRequest(IDWorkRoom=room, SenderResourceId=sender,
            SelectedAgentResourceIds=[sender, other], RawMessage='Quem és?',
            SolidSETInstanceCode='local', SendToSolidSET=False)
        with patch.object(controller, 'get_solidset_instance', return_value=instance) as lookup, \
                patch.object(controller.auto_reply_service, 'get_active_agents_for_workroom', return_value=configured), \
                patch.object(controller, 'get_agent_knowledge', side_effect=lambda owner, room: 'Private ' + owner), \
                patch.object(controller, 'get_agent_reinforcement_context', return_value=''), \
                patch.object(controller, 'get_agent_scope_profile', side_effect=lambda inst, owner: {'FullName': owner}) as profile, \
                patch.object(controller, 'get_active_agent_prompt', return_value={'SystemPrompt': 'Behavior'}) as prompt, \
                patch.object(controller, 'touch_agent_session'), \
                patch.object(controller, '_learn_agent_interaction'), \
                patch.object(controller, '_invoke_orchestrator_for_instance', return_value='Reply') as invoke, \
                patch.object(controller, 'solidset_send_chat_message') as send:
            result = await controller.handle_multi_agent_dialogue(request)
        self.assertEqual(len(result.responses), 2)
        lookup.assert_called_once_with(code='local', source_ip=None)
        self.assertEqual(profile.call_count, 2)
        self.assertEqual(prompt.call_count, 2)
        for call in invoke.call_args_list:
            data = call.kwargs['message_metadata']
            owner = data['agent_resource_id']
            self.assertEqual(data['agent_knowledge'], 'Private ' + owner)
            self.assertEqual(data['agent_profile']['FullName'], owner)
            self.assertEqual(data['sender_resource_id'], str(sender))
            self.assertEqual(data['solidset_instance_id'], str(instance_id))
            self.assertIn(str(instance_id), call.kwargs['session_id'])
            self.assertIn(owner, call.kwargs['session_id'])
            self.assertEqual(call.args, ('local',))
        send.invoke.assert_not_called()

    async def test_unknown_instance_is_rejected_even_without_send(self):
        request = MultiAgentDialogueRequest(IDWorkRoom=uuid4(), SelectedAgentResourceIds=[uuid4()],
            RawMessage='Quem és?', SolidSETInstanceCode='missing')
        with patch.object(controller, 'get_solidset_instance', return_value=None), \
                patch.object(controller, '_invoke_orchestrator_for_instance') as invoke:
            with self.assertRaises(controller.HTTPException) as error:
                await controller.handle_multi_agent_dialogue(request)
        self.assertEqual(error.exception.status_code, 404)
        invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
