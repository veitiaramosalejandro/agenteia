from unittest.mock import patch, Mock
from types import SimpleNamespace
import json
import pytest

import httpx

from app.agent.prompt_budget import compact_system
from app.services.agent_restrictions import _language, _request_instruction, answer_restricted_topic
from app.services.agent_restrictions import _scope_decision


@pytest.mark.parametrize("decision,reason", [
    ("allow", "in_scope"), ("scoped", "scoped_interpretation"),
    ("clarify", "insufficient_context"), ("decline", "explicit_restriction"),
    ("decline", "out_of_scope"),
])
def test_classifier_parses_all_decisions(decision, reason):
    assert _classify_output({"decision": decision, "reason_code": reason}) == decision


def _classify_output(payload):
    config = SimpleNamespace(timeout_seconds=30, provider="openai")
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content=json.dumps(payload))
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model):
        return _scope_decision("Ayúdame a evaluar una idea", {}, "instance", "agent")


@pytest.mark.parametrize("payload", [
    [], {"decision": []}, {"decision": "unknown"},
    {"decision": "allow", "reason_code": "explicit_restriction"},
    {"decision": "scoped", "extra": True}, {"decision": "clarify", "reason_code": []},
])
def test_classifier_rejects_invalid_contract(payload):
    with pytest.raises(ValueError):
        _classify_output(payload)


def test_scoped_request_continues_with_normal_context_and_tools():
    context = {}
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="scoped"), \
         patch("app.services.agent_restrictions._clarification") as clarify:
        assert answer_restricted_topic("Evalúa mi idea", "instance", "agent", scope_context=context) is None
        assert context["_agent_scope_decision"] == "scoped"
        clarify.assert_not_called()


def test_clarify_returns_only_clarification():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="clarify"), \
         patch("app.services.agent_restrictions._clarification", return_value="¿Qué tipo de modelo?"):
        assert answer_restricted_topic("Ayúdame con un modelo", "instance", "agent") == "¿Qué tipo de modelo?"


def test_clarification_failure_does_not_allow_ambiguous_task():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="clarify"), \
         patch("app.services.agent_restrictions._clarification", side_effect=httpx.ReadTimeout("timeout")):
        assert "No puedo confirmar" in answer_restricted_topic("Ayúdame", "instance", "agent")


def test_malformed_policy_fails_closed():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value={"BehaviorConfig": ["invalid"]}):
        assert "No puedo confirmar" in answer_restricted_topic("Ayúdame", "instance", "agent")


PUBLISHED = {
    "BehaviorConfig": {
        "role": "especialista en finanzas corporativas y mercados",
        "specialties": ["Valoración de empresas"],
        "restrictions": ["No proporcionar diagnósticos médicos ni consejos de salud física."],
        "out_of_scope_action": "Declinar amablemente las preguntas ajenas a finanzas.",
        "code_review_instructions": ["No cambies el lenguaje del código."],
        "response_format": ["Problemas por severidad", "Código propuesto"],
    }
}


def test_scope_decision_uses_published_agent_policy_for_any_question():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._clarification", return_value="No puedo responder. Mi ámbito es finanzas corporativas.") as refusal, \
         patch("app.services.agent_restrictions._scope_decision", return_value="decline") as decide:
        answer = answer_restricted_topic(
            "¿Qué ejercicios son buenos para aliviar el dolor lumbar?", "instance", "agent"
        )
    decide.assert_called_once_with(
        "¿Qué ejercicios son buenos para aliviar el dolor lumbar?",
        PUBLISHED["BehaviorConfig"], "instance", "agent",
    )
    assert "finanzas corporativas" in answer
    assert "No puedo responder" in answer
    assert refusal.call_args.kwargs == {"decline": True}


def test_allowed_request_continues_to_agent():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="allow"):
        assert answer_restricted_topic("¿Cómo valorar una empresa por DCF?", "instance", "agent") is None


def test_role_and_specialties_alone_enforce_published_scope():
    policy_without_explicit_restrictions = {
        "BehaviorConfig": {
            "role": "especialista financiero",
            "specialties": ["Análisis financiero", "Mercados"],
        }
    }
    with patch(
        "app.services.agent_restrictions.get_active_agent_prompt",
        return_value=policy_without_explicit_restrictions,
    ), patch(
        "app.services.agent_restrictions._scope_decision",
        return_value="decline",
    ) as decide, patch(
        "app.services.agent_restrictions._clarification",
        return_value="No puedo responder sobre programación; mi ámbito es financiero.",
    ) as refusal:
        answer = answer_restricted_topic(
            "¿Qué sabes de algoritmos de programación?", "instance", "agent"
        )

    decide.assert_called_once()
    refusal.assert_called_once_with(
        "¿Qué sabes de algoritmos de programación?",
        policy_without_explicit_restrictions["BehaviorConfig"],
        "instance",
        "agent",
        "es",
        decline=True,
    )
    assert "programación" in answer


def test_unpublished_policy_does_not_trigger_model_call():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=None), \
         patch("app.services.agent_restrictions._scope_decision") as decide:
        assert answer_restricted_topic("¿Qué ejercicios alivian el dolor lumbar?", "instance", "agent") is None
    decide.assert_not_called()


def test_invalid_scope_decision_fails_closed():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", side_effect=ValueError):
        answer = answer_restricted_topic("¿Qué ejercicios alivian el dolor lumbar?", "instance", "agent")
    assert "No puedo confirmar ahora" in answer


def test_scope_timeout_does_not_bypass_published_scope():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch(
             "app.services.agent_restrictions._scope_decision",
             side_effect=httpx.ReadTimeout("scope classifier timed out"),
         ):
        answer = answer_restricted_topic(
            "Interpreta el código siguiente: private void LostFocus() {}",
            "instance",
            "agent",
        )

    assert "No puedo confirmar" in answer


def test_attached_code_does_not_change_request_language():
    message = (
        "Explícame bien qué está haciendo esta línea de código:\n\n"
        "// Determine if the control that lost focus is a text input control\n"
        "bool isTextControl = e.OriginalSource is TextBox;"
    )
    assert _request_instruction(message) == (
        "Explícame bien qué está haciendo esta línea de código:"
    )
    assert _language(message) == "es"


def test_unknown_detected_language_uses_published_default():
    class Decision:
        language = "eo"

    class Resolver:
        @staticmethod
        def detect(_message):
            return Decision()

    with patch("app.services.agent_restrictions._language_resolver", return_value=Resolver()):
        assert _language("Implementa el algoritmo Floyd en Java", "es") == "es"


def test_compact_ollama_prompt_keeps_published_restrictions():
    prompt = compact_system(
        "", language="español", identity={}, agent_id="agent", subject_id="",
        now="2026-09-16T13:18:00Z", business_query=False, auto_reply=True,
        agent_behavior=PUBLISHED["BehaviorConfig"],
    )
    assert "No proporcionar diagnósticos médicos" in prompt
    assert "Declinar amablemente" in prompt
    assert "especialista en finanzas corporativas" in prompt
    assert "No cambies el lenguaje del código" in prompt
    assert "1. Problemas por severidad" in prompt


@pytest.mark.parametrize('first,code', [
    ('not json SECRET', 'invalid_json'),
    ('[]', 'invalid_object'),
    ('{"decision":"allow","extra":true}', 'invalid_fields'),
    ('{"decision":"unknown","reason_code":"in_scope"}', 'invalid_decision'),
    ('{"decision":"allow","reason_code":"out_of_scope"}', 'invalid_reason_pair'),
])
def test_invalid_output_is_repaired_once_without_logging_content(first, code, capsys):
    from app.services.agent_restrictions import _SCOPE_SCHEMA
    config = SimpleNamespace(timeout_seconds=30, provider='ollama')
    model = Mock()
    model.bind.return_value = model
    model.invoke.side_effect = [
        SimpleNamespace(content=first),
        SimpleNamespace(content='{"decision":"clarify","reason_code":"insufficient_context"}'),
    ]
    with patch('app.services.agent_restrictions.get_llm_provider_configuration', return_value={'id': 'model'}), \
         patch('app.services.agent_restrictions.provider_config_from_record', return_value=config), \
         patch('app.services.agent_restrictions.replace', return_value=config), \
         patch('app.services.agent_restrictions.create_chat_model', return_value=model):
        assert _scope_decision('Solicitud ambigua', {}, 'instance', 'agent') == 'clarify'
    assert model.invoke.call_count == 2
    model.bind.assert_called_once_with(format=_SCOPE_SCHEMA)
    output = capsys.readouterr().out
    assert code in output
    assert 'SECRET' not in output
    assert 'SECRET' not in str(model.invoke.call_args.args)


@pytest.mark.parametrize('second', [
    SimpleNamespace(content='invalid again'),
    httpx.ReadTimeout('sensitive failure'),
])
def test_failed_repair_stops_without_authorizing(second):
    config = SimpleNamespace(timeout_seconds=30, provider='openai')
    model = Mock()
    model.invoke.side_effect = [SimpleNamespace(content='invalid'), second]
    with patch('app.services.agent_restrictions.get_active_agent_prompt', return_value=PUBLISHED), \
         patch('app.services.agent_restrictions._language', return_value='es'), \
         patch('app.services.agent_restrictions.get_llm_provider_configuration', return_value={'id': 'model'}), \
         patch('app.services.agent_restrictions.provider_config_from_record', return_value=config), \
         patch('app.services.agent_restrictions.replace', return_value=config), \
         patch('app.services.agent_restrictions.create_chat_model', return_value=model):
        context = {}
        answer = answer_restricted_topic('Solicitud', 'instance', 'agent', scope_context=context)
    assert 'No puedo confirmar' in answer
    assert model.invoke.call_count == 2
    assert '_agent_scope_decision' not in context


def test_refusal_failure_returns_localized_redirection():
    with patch('app.services.agent_restrictions.get_active_agent_prompt', return_value=PUBLISHED), \
         patch('app.services.agent_restrictions._language', return_value='pt'), \
         patch('app.services.agent_restrictions._scope_decision', return_value='decline'), \
         patch('app.services.agent_restrictions._clarification', side_effect=httpx.ReadTimeout('failed')):
        answer = answer_restricted_topic('Pedido', 'instance', 'agent')
    assert 'Posso ajudar' in answer
    assert 'finanzas' not in answer


@pytest.mark.parametrize('timeout', [20, 180, 900])
@pytest.mark.parametrize('stage', ['classification', 'clarification', 'refusal'])
def test_scope_stages_respect_provider_timeout(timeout, stage):
    from app.llm.providers import LLMProviderConfig
    from app.services.agent_restrictions import _clarification
    config = LLMProviderConfig(provider='ollama', model='test-model', timeout_seconds=timeout)
    model = Mock()
    model.bind.return_value = model
    model.invoke.return_value = SimpleNamespace(content=(
        '{"decision":"clarify","reason_code":"insufficient_context"}'
        if stage == 'classification' else 'Respuesta breve'
    ))
    with patch('app.services.agent_restrictions.get_llm_provider_configuration', return_value={'id': 'model'}), \
         patch('app.services.agent_restrictions.provider_config_from_record', return_value=config), \
         patch('app.services.agent_restrictions.create_chat_model', return_value=model) as create:
        if stage == 'classification':
            _scope_decision('Solicitud', {}, 'instance', 'agent')
        else:
            _clarification('Solicitud', {}, 'instance', 'agent', 'es', decline=stage == 'refusal')
    effective = create.call_args.args[0]
    assert effective.timeout_seconds == timeout
    assert effective.max_retries == 0
    assert model.invoke.call_count == 1


def test_scope_diagnostic_escapes_and_bounds_fields():
    from app.services.agent_restrictions import _parse_scope_output, ScopeOutputError
    reason = 'bad\n\r\t\x1b' + 'x' * 100
    with pytest.raises(ScopeOutputError) as caught:
        _parse_scope_output(json.dumps({'decision': 'scoped', 'reason_code': reason}))
    error = caught.value
    assert str(error) == 'invalid_reason_pair'
    assert error.decision == '"scoped"'
    assert json.loads(error.reason_code) == reason[:80] + '...'
    assert '\n' not in error.reason_code
    assert '\r' not in error.reason_code
    assert '\x1b' not in error.reason_code


def test_scope_diagnostic_does_not_serialize_nested_values():
    from app.services.agent_restrictions import _parse_scope_output, ScopeOutputError
    with pytest.raises(ScopeOutputError) as caught:
        _parse_scope_output(json.dumps({'decision': 'scoped', 'reason_code': {'secret': 'PRIVATE'}}))
    assert caught.value.reason_code == '"<dict>"'
    assert 'PRIVATE' not in str(vars(caught.value))


def test_scope_diagnostic_reports_pair_attempt_and_model(capsys):
    from app.llm.providers import LLMProviderConfig
    config = LLMProviderConfig(provider='openai', model='diagnostic-test')
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content='{"decision":"scoped","reason_code":"in_scope"}')
    with patch('app.services.agent_restrictions.get_llm_provider_configuration', return_value={'id': 'model'}), \
         patch('app.services.agent_restrictions.provider_config_from_record', return_value=config), \
         patch('app.services.agent_restrictions.create_chat_model', return_value=model), \
         pytest.raises(ValueError):
        _scope_decision('PRIVATE QUESTION', {}, 'instance', 'agent')
    logs = capsys.readouterr().out
    assert 'attempt=1' in logs and 'attempt=2' in logs
    assert 'decision="scoped" reason_code="in_scope"' in logs
    assert 'provider="openai" model="diagnostic-test"' in logs
    assert 'PRIVATE QUESTION' not in logs


@pytest.mark.parametrize('decision,reason', [
    ('allow', 'in_scope'), ('scoped', 'scoped_interpretation'),
    ('clarify', 'insufficient_context'), ('decline', 'out_of_scope'),
])
def test_single_decision_derives_reason_without_second_model_choice(decision, reason):
    from app.services.agent_restrictions import _parse_scope_output
    assert _parse_scope_output(json.dumps({'decision': decision})) == (decision, reason)


def test_external_web_capability_instruction_covers_entity_facts():
    from app.services.agent_restrictions import _scope_decision
    config = SimpleNamespace(timeout_seconds=30, provider='openai', model='test')
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content='{"decision":"scoped"}')
    with patch('app.services.agent_restrictions.get_llm_provider_configuration', return_value={'id':'model'}), \
         patch('app.services.agent_restrictions.provider_config_from_record', return_value=config), \
         patch('app.services.agent_restrictions.replace', return_value=config), \
         patch('app.services.agent_restrictions.create_chat_model', return_value=model):
        assert _scope_decision('¿Qué tipos de modelos genera FinModeler?', {}, 'instance', 'agent', {'external_web'}) == 'scoped'
    prompt = model.invoke.call_args.args[0][0].content
    assert 'entidad externa identificada' in prompt
    assert 'external_web' in prompt


def test_external_web_converts_ambiguous_scope_to_scoped():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="clarify"):
        context = {"declared_capabilities": {"external_web"}}
        assert answer_restricted_topic(
            "¿Qué ofrece una herramienta financiera?", "instance", "agent",
            scope_context=context,
        ) is None
    assert context["_agent_scope_decision"] == "scoped"
