from app.agent.capabilities import has_visual_capability, normalize_capabilities
from app.llm.routing import requested_capability
from app.llm.providers import LLMProviderConfig


def test_multimodal_is_normalized_to_visual_capability():
    assert normalize_capabilities(["multimodal"]) == {"vision"}
    assert normalize_capabilities(["imagen"]) == {"vision"}
    assert has_visual_capability(["vision"])
    assert has_visual_capability(["multimodal"])
    assert has_visual_capability(["imagen"])


def test_message_attachment_requires_vision_route():
    assert requested_capability(
        "Analiza esta imagen",
        {"attachment_references": ["60bfbda9-e12e-4ff5-8651-98714609f0b3"]},
    ) == "vision"


def test_ollama_vision_probe_sends_image_payload(monkeypatch):
    from app.services import model_capability_validation as validation

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "vision-model"}]}

    observed = {}

    monkeypatch.setattr(validation.httpx, "get", lambda *args, **kwargs: Response())

    def fake_invoke(config, **kwargs):
        observed.update(kwargs)
        return {"message": {"content": "Imagen recibida"}}, 3.0

    monkeypatch.setattr(validation, "_invoke_ollama", fake_invoke)
    result = validation.validate_provider(
        LLMProviderConfig(provider="ollama", model="vision-model", base_url="http://ollama"),
        ["imagen"],
    )

    assert result["passed"] is True
    assert result["capabilities"][0]["capability"] == "vision"
    assert observed["vision"] is True
