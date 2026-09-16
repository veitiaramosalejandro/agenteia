from unittest.mock import patch

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


def test_published_medical_restriction_blocks_lumbar_exercise_advice():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=PUBLISHED):
        answer = answer_restricted_topic(
            "¿Qué ejercicios son buenos para aliviar el dolor lumbar?", "instance", "agent"
        )
    assert "finanzas corporativas" in answer
    assert "No puedo aconsejarte" in answer
    assert "sentadillas" not in answer


def test_unpublished_restriction_does_not_claim_a_policy():
    with patch("app.services.agent_restrictions.get_active_agent_prompt", return_value=None):
        assert answer_restricted_topic("¿Qué ejercicios alivian el dolor lumbar?", "instance", "agent") is None


def test_financial_question_is_not_blocked_by_medical_restriction():
    with patch("app.services.agent_restrictions.get_active_agent_prompt") as lookup:
        assert answer_restricted_topic("¿Cómo valorar una empresa por DCF?", "instance", "agent") is None
    lookup.assert_not_called()


def test_compact_ollama_prompt_keeps_published_restrictions():
    prompt = compact_system(
        "", language="español", identity={}, agent_id="agent", subject_id="",
        now="2026-09-16T13:18:00Z", business_query=False, auto_reply=True,
        agent_behavior=PUBLISHED["BehaviorConfig"],
    )
    assert "No proporcionar diagnósticos médicos" in prompt
    assert "Declinar amablemente" in prompt
    assert "especialista en finanzas corporativas" in prompt
