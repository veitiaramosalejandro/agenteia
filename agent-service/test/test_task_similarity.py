from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from app.api.schemas.task_junta import TaskJuntaData
from app.llm import LLMProviderConfig
from app.services.task_similarity import _ask_model, _all_pairs


class FakeSimilarityModel:
    def __init__(self, response: str):
        self.response = response

    def bind(self, **kwargs):
        return self

    def invoke(self, messages):
        return SimpleNamespace(content=self.response)


def test_contradictory_same_work_reason_is_discarded():
    first = TaskJuntaData(IDTask=uuid4(), ShortName="Bloquear copias")
    second = TaskJuntaData(IDTask=uuid4(), ShortName="Instalar Docker")
    response = (
        '{"evaluations":[{"leftId":"%s","rightId":"%s",'
        '"decision":"same_work","reason":"Tareas diferentes, objetivos funcionales diferentes"}]}'
        % (first.IDTask, second.IDTask)
    )

    with patch(
        "app.services.task_similarity.provider_config_from_settings",
        return_value=LLMProviderConfig(provider="ollama", model="test"),
    ):
        matches = _ask_model(
            {first.IDTask: first, second.IDTask: second},
            _all_pairs([first, second]),
            model_factory=lambda: FakeSimilarityModel(response),
        )

    assert matches == []


def test_different_decision_never_creates_merge():
    first = TaskJuntaData(IDTask=uuid4(), ShortName="Bloquear copias")
    second = TaskJuntaData(IDTask=uuid4(), ShortName="Instalar Docker")
    response = (
        '{"evaluations":[{"leftId":"%s","rightId":"%s",'
        '"decision":"different","reason":"Objetivos distintos"}]}'
        % (first.IDTask, second.IDTask)
    )

    with patch(
        "app.services.task_similarity.provider_config_from_settings",
        return_value=LLMProviderConfig(provider="ollama", model="test"),
    ):
        matches = _ask_model(
            {first.IDTask: first, second.IDTask: second},
            _all_pairs([first, second]),
            model_factory=lambda: FakeSimilarityModel(response),
        )

    assert matches == []
