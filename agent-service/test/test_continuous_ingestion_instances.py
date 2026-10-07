from contextlib import contextmanager

import pytest

from app import main


def _instance(code: str) -> dict:
    return {
        "Code": code,
        "DataAPI": {"active": True, "BaseUrl": f"http://{code}:8080"},
    }


def test_continuous_ingestion_sets_context_and_allows_partial_failure(monkeypatch):
    active_contexts: list[str] = []

    @contextmanager
    def fake_context(instance):
        active_contexts.append(instance["Code"])
        try:
            yield
        finally:
            active_contexts.pop()

    def fake_ingest(*, instance_code=None):
        assert active_contexts == [instance_code]
        if instance_code == "broken":
            raise RuntimeError("offline")
        return {"instanceCode": instance_code, "canales": 2}

    monkeypatch.setattr(
        main,
        "list_active_solidset_instances",
        lambda: [_instance("broken"), _instance("working")],
    )
    monkeypatch.setattr(main, "solidset_sql_instance_context", fake_context)
    monkeypatch.setattr(main, "ingestar_sistema_completo", fake_ingest)

    result = main._ingestar_instancias_solidset_activas()

    assert result["status"] == "partial"
    assert result["instances"]["working"]["canales"] == 2
    assert result["errors"] == {"broken": "offline"}
    assert active_contexts == []


def test_continuous_ingestion_fails_without_eligible_instances(monkeypatch):
    monkeypatch.setattr(
        main,
        "list_active_solidset_instances",
        lambda: [{"Code": "disabled", "DataAPI": {"active": False}}],
    )

    with pytest.raises(RuntimeError, match="Data API"):
        main._ingestar_instancias_solidset_activas()
