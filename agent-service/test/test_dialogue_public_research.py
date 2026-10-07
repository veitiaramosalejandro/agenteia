import unittest
from unittest.mock import patch
from app.api.controllers import agent_management as controller
from app.services.external_search import ExternalSearchResult
from app.services.openai_direct import _twin_context
import json


class DialoguePublicResearchTests(unittest.TestCase):
    def test_current_weather_uses_selected_agent_and_preserves_sources(self):
        with patch.object(controller, 'get_agent_model_configurations', return_value=[
            {'Capabilities': ['general']}, {'Capabilities': '["external_web"]'}
        ]), patch.object(controller, 'search_with_openai', return_value=[
            ExternalSearchResult('Weather', 'Observation at 12:00: 21 C', 'https://example.org/weather')
        ]) as search:
            result = controller._dialogue_public_research('Dime temperatura actual de Leiria?', 'selected')
        search.assert_called_once_with('Dime temperatura actual de Leiria?', resource_id='selected')
        self.assertEqual(result['status'], 'completed')
        context = json.loads(_twin_context({'source': 'solidset_multi_agent', 'public_research': result}))
        self.assertEqual(context['public_research']['sources'][0]['url'], 'https://example.org/weather')
        self.assertIn('checked_at_utc', result)

    def test_no_capability_means_no_public_search(self):
        with patch.object(controller, 'get_agent_model_configurations', return_value=[{'Capabilities': ['general']}]), \
                patch.object(controller, 'search_with_openai') as search:
            result = controller._dialogue_public_research('Temperatura atual de Leiria?', 'selected')
        self.assertEqual(result['status'], 'not_permitted')
        search.assert_not_called()

    def test_failure_does_not_leak_provider_details_or_invent_weather(self):
        with patch.object(controller, 'get_agent_model_configurations', return_value=[{'Capabilities': ['external_web']}]), \
                patch.object(controller, 'search_with_openai', side_effect=RuntimeError('private-key')):
            result = controller._dialogue_public_research('Temperatura atual de Leiria?', 'selected')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('private-key', json.dumps(result))
        self.assertNotIn('sources', result)

    def test_identity_question_does_not_search_public_web(self):
        with patch.object(controller, 'get_agent_model_configurations') as policies, \
                patch.object(controller, 'search_with_openai') as search:
            self.assertEqual(controller._dialogue_public_research('Quem es?', 'selected'), {})
        policies.assert_not_called()
        search.assert_not_called()


if __name__ == '__main__':
    unittest.main()
