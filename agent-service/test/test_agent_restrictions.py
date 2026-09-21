from unittest.mock import patch

import httpx

from app.agent.prompt_budget import compact_system
from app.services.agent_restrictions import answer_restricted_topic


PUBLISHED = {
    "BehaviorConfig": {
        "role": "especialista en finanzas corporativas y mercados",
        "specialties": ["Valoración de empresas"],
        "restrictions": ["No proporcionar diagnósticos médicos ni consejos de salud física."],
        "out_of_scope_action": "Declinar amablemente las preguntas ajenas a finanzas.",
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


def test_compact_ollama_prompt_keeps_published_restrictions():
    prompt = compact_system(
        "", language="español", identity={}, agent_id="agent", subject_id="",
        now="2026-09-16T13:18:00Z", business_query=False, auto_reply=True,
        agent_behavior=PUBLISHED["BehaviorConfig"],
    )
    assert "No proporcionar diagnósticos médicos" in prompt
    assert "Declinar amablemente" in prompt
    assert "especialista en finanzas corporativas" in prompt
