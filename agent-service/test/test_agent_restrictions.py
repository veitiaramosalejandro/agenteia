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
    {"decision": "scoped"}, {"decision": "clarify", "reason_code": []},
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


def test_allowed_request_continues_to_agent():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED), \
         patch("app.services.agent_restrictions._scope_decision", return_value="allow"):
        assert answer_restricted_topic("¿Cómo valorar una empresa por DCF?", "instance", "agent") is None


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


def test_scope_timeout_continues_with_published_prompt():
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

    assert answer is None


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
