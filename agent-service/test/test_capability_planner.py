import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from app.llm.providers import LLMProviderConfig
from app.services.capability_planner import prepare_capability_route


@pytest.mark.parametrize('selected', ['general', 'external_web', 'reasoning', 'coding', 'sql'])
def test_planner_selects_declared_options_and_preserves_scope(selected):
    model = Mock()
    model.bind.return_value = model
    model.invoke.return_value = SimpleNamespace(content=json.dumps({'capability': selected}))
    metadata = {'agent_resource_id': 'agent', 'solidset_instance_id': 'instance', '_agent_scope_decision': 'scoped'}
    with patch('app.services.capability_planner.get_agent_model_configurations', return_value=[{'Capabilities': ['external_web', 'reasoning', 'coding', 'sql']}]) as assignments, \
         patch('app.services.capability_planner.get_active_agent_prompt', return_value={'BehaviorConfig': {'role': 'specialist'}}), \
         patch('app.services.capability_planner.get_llm_provider_configuration', return_value={'id': 'model'}), \
         patch('app.services.capability_planner.provider_config_from_record', return_value=LLMProviderConfig(provider='ollama', model='test')), \
         patch('app.services.capability_planner.create_chat_model', return_value=model):
        prepare_capability_route('Compare available options', metadata)
        prepare_capability_route('Compare available options', metadata)
    assert model.invoke.call_count == 1
    assignments.assert_called_once_with('agent', 'instance')
    assert metadata['model_capability'] == selected
    assert metadata['external_information_mode'] == (selected == 'external_web')
    assert metadata['_agent_scope_decision'] == 'scoped'


def test_default_assignment_does_not_grant_web_permission():
    metadata = {'agent_resource_id': 'agent', 'solidset_instance_id': 'instance'}
    with patch('app.services.capability_planner.get_agent_model_configurations', return_value=[{'Capabilities': ['general'], 'IsDefault': True}]), \
         patch('app.services.capability_planner.create_chat_model') as create:
        prepare_capability_route('Research this', metadata)
    assert metadata['model_capability'] == 'general'
    assert 'external_web' not in metadata['tool_permissions']
    create.assert_not_called()


def test_planner_rejects_undeclared_capability():
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content='{"capability":"sql"}')
    metadata = {'agent_resource_id': 'agent', 'solidset_instance_id': 'instance'}
    with patch('app.services.capability_planner.get_agent_model_configurations', return_value=[{'Capabilities': ['external_web']}]), \
         patch('app.services.capability_planner.get_active_agent_prompt', return_value={}), \
         patch('app.services.capability_planner.get_llm_provider_configuration', return_value={'id': 'model'}), \
         patch('app.services.capability_planner.provider_config_from_record', return_value=LLMProviderConfig(provider='openai', model='test')), \
         patch('app.services.capability_planner.create_chat_model', return_value=model), \
         pytest.raises(ValueError, match='invalid_capability_plan'):
        prepare_capability_route('Request', metadata)
    assert 'model_capability' not in metadata


def test_explicit_permission_limit_is_not_expanded():
    metadata = {'agent_resource_id': 'agent', 'solidset_instance_id': 'instance', 'tool_permissions': set()}
    with patch('app.services.capability_planner.get_agent_model_configurations', return_value=[{'Capabilities': ['external_web']}]), \
         patch('app.services.capability_planner.create_chat_model') as create:
        prepare_capability_route('Request', metadata)
    assert metadata['tool_permissions'] == set()
    create.assert_not_called()


def test_declared_tool_alias_routes_to_web():
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content='{"capability":"tool:google_web_search"}')
    metadata = {'agent_resource_id': 'agent', 'solidset_instance_id': 'instance'}
    with patch('app.services.capability_planner.get_agent_model_configurations', return_value=[{'Capabilities': ['tool:google_web_search']}]), \
         patch('app.services.capability_planner.get_active_agent_prompt', return_value={}), \
         patch('app.services.capability_planner.get_llm_provider_configuration', return_value={'id': 'model'}), \
         patch('app.services.capability_planner.provider_config_from_record', return_value=LLMProviderConfig(provider='openai', model='test')), \
         patch('app.services.capability_planner.create_chat_model', return_value=model):
        prepare_capability_route('Compare public options', metadata)
    assert metadata['model_capability'] == 'tool:google_web_search'
    assert metadata['external_information_mode'] is True


def test_scoped_research_cannot_bypass_policy_synthesis():
    from app.services.openai_direct import answer_direct
    metadata = {'_agent_scope_decision': 'scoped', 'external_information_mode': True}
    with patch('app.services.openai_direct.assigned_openai') as assigned:
        assert answer_direct('Request', metadata, 'session') is None
    assigned.assert_not_called()
