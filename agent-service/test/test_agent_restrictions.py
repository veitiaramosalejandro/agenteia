from unittest.mock import patch, Mock
from types import SimpleNamespace
import json
import pytest

import httpx

from app.agent.prompt_budget import compact_system
from app.services.agent_restrictions import (
    _clarification,
    _language,
    _request_instruction,
    answer_restricted_topic,
)
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
        return _scope_decision(
            "Ayúdame a evaluar una idea", {}, "instance", "agent",
            published_prompt="Prompt publicado de prueba",
        )


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


def test_scoped_cannot_grant_cross_domain_reasoning_or_coding():
    context = {"declared_capabilities": {"reasoning", "coding"}}
    model = Mock()
    model.invoke.side_effect = [
        SimpleNamespace(content='{"decision":"decline"}'),
        SimpleNamespace(content='{"decision":"decline"}'),
    ]
    config = SimpleNamespace(timeout_seconds=30, provider="openai", model="test")
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model), \
         patch("app.services.agent_restrictions._normalize_requested_action", return_value="desarrollar un sistema web"), \
         patch("app.services.agent_restrictions._clarification", return_value="No puedo responder sobre programación; mi ámbito es financiero.") as refusal:
        answer = answer_restricted_topic(
            "Necesito hacer un sistema web", "instance", "agent", scope_context=context
        )
    assert context["_agent_scope_decision"] == "decline"
    refusal.assert_not_called()
    assert "No puedo responder" in answer
    assert "programación" not in answer


def test_scope_classifier_uses_published_code_review_policy():
    policy = {
        "role": "ingeniero de software senior",
        "specialties": ["Backend C#"],
        "code_review_instructions": ["Revisa tipos, excepciones y concurrencia."],
        "restrictions": ["No generar malware ni facilitar accesos no autorizados."],
    }
    config = SimpleNamespace(timeout_seconds=30, provider="openai", model="test")
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content='{"decision":"scoped"}')
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model), \
         patch("app.services.agent_restrictions._normalize_requested_action", return_value="refactorizar código C#"):
        from app.services.agent_restrictions import _scope_decision
        decision = _scope_decision(
            "Refactoriza: public async Task Foo() { return; }",
            policy, "instance", "agent",
            published_prompt=json.dumps(policy, ensure_ascii=False),
        )
    assert decision == "scoped"


def test_published_programming_scope_allows_code_request_without_artifact():
    policy = {
        "role": "ingeniero de software senior",
        "specialties": ["Backend C#"],
        "code_review_instructions": ["Propón código compilable y pruebas."],
        "restrictions": [],
    }
    config = SimpleNamespace(timeout_seconds=30, provider="openai", model="test")
    model = Mock()
    model.invoke.side_effect = [
        SimpleNamespace(content='{"decision":"scoped"}'),
        SimpleNamespace(content='{"decision":"decline"}'),
    ]
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model), \
         patch("app.services.agent_restrictions._normalize_requested_action", return_value="implementar un método de hola mundo"):
        from app.services.agent_restrictions import _scope_decision
        decision = _scope_decision(
            "Implementa un método de hola mundo", policy, "instance", "agent",
            published_prompt=json.dumps(policy, ensure_ascii=False),
        )
    assert decision == "scoped"


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
    "SystemPrompt": (
        "OBJETIVO\nAyudar con finanzas corporativas y mercados.\n"
        "ESPECIALIDADES VERIFICADAS\n- Valoración de empresas.\n"
        "RESTRIÇÕES DE ESPECIALIDADE\n"
        "- No proporcionar diagnósticos médicos ni consejos de salud física."
    ),
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
        published_prompt=PUBLISHED["SystemPrompt"],
        conversation_history="",
    )
    assert "No puedo responder" in answer
    assert "finanzas corporativas" not in answer
    refusal.assert_not_called()


def test_allowed_request_continues_to_agent():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="allow"):
        assert answer_restricted_topic("¿Cómo valorar una empresa por DCF?", "instance", "agent") is None


def test_scope_classifier_receives_intervenient_history_as_context_only():
    config = SimpleNamespace(timeout_seconds=30, provider="openai", model="test")
    model = Mock()
    model.invoke.return_value = SimpleNamespace(
        content='{"decision":"allow","reason_code":"in_scope"}'
    )
    history = "[2026-10-21 12:44:00] Gemelo [IA] (agent): Kanban es un método ágil."
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model):
        assert _scope_decision(
            "¿Qué es Kanban?", PUBLISHED["BehaviorConfig"], "instance", "agent",
        published_prompt=PUBLISHED["SystemPrompt"], conversation_history=history,
        ) == "allow"

    payload = json.loads(model.invoke.call_args.args[0][-1].content)
    assert payload["requested_task"] == "¿Qué es Kanban?"
    assert payload["conversation_history"] == history


def test_channel_follow_up_keeps_the_topic_from_intervenient_history():
    context = {
        "conversation_history": (
            "[2026-10-21 12:43:00] Victor: Kanban es un método ágil "
            "para gestionar el flujo de trabajo."
        )
    }
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="scoped") as decide, \
         patch("app.services.agent_restrictions._clarification") as clarify:
        assert answer_restricted_topic(
            "¿Qué es Kanban?", "instance", "agent", scope_context=context
        ) is None

    decide.assert_called_once_with(
        "¿Qué es Kanban?", PUBLISHED["BehaviorConfig"], "instance", "agent",
        published_prompt=PUBLISHED["SystemPrompt"],
        conversation_history=context["conversation_history"],
    )
    clarify.assert_not_called()
    assert context["_agent_scope_decision"] == "scoped"


def test_vague_clarification_follow_up_inherits_conversation_scope():
    context = {
        "conversation_history": (
            "[2026-10-22 12:00:00] Usuario: ¿Cómo configuro N8N en Docker?\n"
            "[2026-10-22 12:01:00] Dev20 [IA]: Explicación sobre Docker y N8N."
        )
    }
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="scoped") as decide, \
         patch("app.services.agent_restrictions._clarification") as clarify:
        assert answer_restricted_topic(
            "No entendi correctamente explicame bien por favor?",
            "instance", "agent", scope_context=context,
        ) is None

    decide.assert_called_once_with(
        "No entendi correctamente explicame bien por favor?",
        PUBLISHED["BehaviorConfig"], "instance", "agent",
        published_prompt=PUBLISHED["SystemPrompt"],
        conversation_history=context["conversation_history"],
    )
    clarify.assert_not_called()
    assert context["_agent_scope_decision"] == "scoped"


def test_clarification_rejects_internal_policy_leak():
    config = SimpleNamespace(timeout_seconds=30, provider="openai", model="test")
    model = Mock()
    model.invoke.return_value = SimpleNamespace(
        content="La solicitud ya fue clasificada fuera del ámbito permitido. "
        "Aplica out_of_scope_action."
    )
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model), \
         pytest.raises(ValueError, match="internal_contract_leak"):
        _clarification(
            "¿Qué es Kanban?", PUBLISHED["BehaviorConfig"], "instance", "agent", "es",
            decline=True, published_prompt=PUBLISHED["SystemPrompt"],
        )


def test_role_and_specialties_alone_enforce_published_scope():
    policy_without_explicit_restrictions = {
        "SystemPrompt": "ESPECIALIDADES VERIFICADAS\n- Análisis financiero.\n- Mercados.",
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
    refusal.assert_not_called()
    assert "No puedo responder" in answer
    assert "programación" not in answer


def test_unpublished_policy_does_not_trigger_model_call():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=None), \
         patch("app.services.agent_restrictions._scope_decision") as decide:
        assert "No puedo confirmar" in answer_restricted_topic(
            "¿Qué ejercicios alivian el dolor lumbar?", "instance", "agent"
        )
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
        assert _scope_decision(
            'Solicitud ambigua', {}, 'instance', 'agent',
            published_prompt='Prompt publicado',
        ) == 'clarify'
    assert model.invoke.call_count == 2
    model.bind.assert_called_once_with(format=_SCOPE_SCHEMA, think=False)
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


def test_decline_returns_localized_safe_redirection_without_second_llm_call():
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
            _scope_decision(
                'Solicitud', {}, 'instance', 'agent',
                published_prompt=PUBLISHED['SystemPrompt'],
            )
        else:
            _clarification(
                'Solicitud', {}, 'instance', 'agent', 'es',
                decline=stage == 'refusal', published_prompt=PUBLISHED['SystemPrompt'],
            )
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
        _scope_decision(
            'PRIVATE QUESTION', {}, 'instance', 'agent',
            published_prompt=PUBLISHED['SystemPrompt'],
        )
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
        assert _scope_decision(
            '¿Qué tipos de modelos genera FinModeler?', {}, 'instance', 'agent',
            published_prompt=PUBLISHED['SystemPrompt'],
        ) == 'scoped'
    payload = json.loads(model.invoke.call_args.args[0][-1].content)
    assert "declared_capabilities" not in payload


def test_external_web_does_not_change_scope_decision():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="clarify"):
        context = {"declared_capabilities": {"external_web"}}
        answer = answer_restricted_topic(
            "¿Qué ofrece una herramienta financiera?", "instance", "agent",
            scope_context=context,
        )
    assert answer is not None
    assert context["_agent_scope_decision"] == "clarify"


def test_scope_prompt_uses_published_policy_without_embedded_domain_rules():
    config = SimpleNamespace(timeout_seconds=30, provider="openai", model="test")
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content='{"decision":"allow"}')
    policy = {
        "role": "ingeniero de software senior",
        "specialties": ["Algoritmia y ciencias de la computación"],
        "out_of_scope_action": "Declinar preguntas ajenas al software.",
    }
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model):
        published_prompt = json.dumps(policy, ensure_ascii=False)
        assert _scope_decision(
            "¿Cuál es la raíz décima de 1024?", policy, "instance", "agent",
            published_prompt=published_prompt,
        ) == "allow"

    payload = json.loads(model.invoke.call_args.args[0][-1].content)
    assert payload["requested_task"] == "¿Cuál es la raíz décima de 1024?"
    assert published_prompt in model.invoke.call_args.args[0][0].content
    assert "operaciones matemáticas básicas" not in model.invoke.call_args.args[0][0].content


def test_scope_classifier_prefers_the_stored_published_system_prompt():
    config = SimpleNamespace(timeout_seconds=30, provider="openai", model="test")
    model = Mock()
    model.invoke.return_value = SimpleNamespace(content='{"decision":"scoped"}')
    published_prompt = (
        "ESPECIALIDADES VERIFICADAS\n"
        "- Algoritmos y estructuras de datos\n"
        "RESTRIÇÕES DE ESPECIALIDADE\n"
        "- No inventar resultados."
    )
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model):
        assert _scope_decision(
            "Factorial de n3?", {"specialties": ["otro ámbito"]},
            "instance", "agent", published_prompt=published_prompt,
        ) == "scoped"

    payload = json.loads(model.invoke.call_args.args[0][-1].content)
    assert payload["requested_task"] == "Factorial de n3?"
    assert published_prompt in model.invoke.call_args.args[0][0].content


def test_visual_agent_policy_is_not_rejected_by_scope_input_guard():
    """The published visual profile must reach the classifier before routing."""
    config = SimpleNamespace(timeout_seconds=30, provider="ollama", model="gemma4:31b")
    model = Mock()
    model.bind.return_value = model
    model.invoke.return_value = SimpleNamespace(content='{"decision":"allow"}')
    policy = {
        "role": "Developer Senior Full-Stack",
        "objective": (
            "Ayudar con software, infraestructura y SolidSET. Analizar imágenes "
            "proporcionadas por el usuario cuando la consulta solicite explicar, "
            "describir, comparar o extraer información visible en ellas."
        ),
        "specialties": [
            "Desarrollo full-stack y revisión de código.",
            "Análisis e interpretación de imágenes adjuntas.",
            "Descripción de capturas de pantalla, diagramas, documentos e interfaces.",
            "Lectura de texto visible en imágenes y relación con consultas de software.",
        ],
        "code_review_instructions": ["Propón cambios compilables y pruebas."],
        # Mirrors the size of the published profile currently used by the
        # agent, which is above the former 6,000-character guard.
        "response_format": [
            f"Regla de respuesta {index}: " + "Mantén el análisis verificable y accionable. " * 12
            for index in range(16)
        ],
        "restrictions": ["No inventes datos que no sean visibles o verificables."],
        "out_of_scope_action": (
            "Permitir analizar imágenes adjuntas cuando el agente tenga la capacidad "
            "imagen/vision asignada y la consulta lo solicite."
        ),
    }
    policy_size = len(json.dumps(policy, ensure_ascii=False))
    assert policy_size > 6000
    assert policy_size <= 12000
    with patch("app.services.agent_restrictions.get_llm_provider_configuration", return_value={"id": "model"}), \
         patch("app.services.agent_restrictions.provider_config_from_record", return_value=config), \
         patch("app.services.agent_restrictions.replace", return_value=config), \
         patch("app.services.agent_restrictions.create_chat_model", return_value=model):
        assert _scope_decision(
            "[imagen adjunta: captura.png] Que significa para esta imagen?",
            policy,
            "instance",
            "agent",
            published_prompt=json.dumps(policy, ensure_ascii=False),
        ) == "allow"

    payload = json.loads(model.invoke.call_args.args[0][-1].content)
    assert "declared_capabilities" not in payload
    assert "Análisis e interpretación de imágenes adjuntas." in model.invoke.call_args.args[0][0].content
