from unittest.mock import patch

from app.services.agent_specialty import answer_agent_specialty_question


def test_specialty_question_uses_only_published_agent_configuration():
    with patch("app.services.agent_specialty.get_active_agent_prompt", return_value=None) as lookup:
        answer = answer_agent_specialty_question("Que tipo de especialista eres?", "instance", "agent")
    assert "No tengo una especialidad configurada y publicada" in answer
    lookup.assert_called_once_with("instance", "agent")


def test_specialty_question_reports_published_role_and_specialties():
    published = {"BehaviorConfig": {"role": "analista financiero", "specialties": ["Valuación DCF"]}}
    with patch("app.services.agent_specialty.get_active_agent_prompt", return_value=published):
        answer = answer_agent_specialty_question("¿Cuál es tu especialidad?", "instance", "agent")
    assert "analista financiero" in answer
    assert "Valuación DCF" in answer


def test_unrelated_question_does_not_read_agent_configuration():
    with patch("app.services.agent_specialty.get_active_agent_prompt") as lookup:
        assert answer_agent_specialty_question("¿Qué temperatura hace hoy?", "instance", "agent") is None
    lookup.assert_not_called()
