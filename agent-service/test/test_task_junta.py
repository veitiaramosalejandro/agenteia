from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.controllers.task_junta import router


def test_receive_junta_tasks_accepts_list():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    task_id = uuid4()

    with patch(
        "app.api.controllers.task_junta.analyze_task_similarity",
        return_value={"tasks": [], "mergeGroups": []},
    ):
        response = client.post(
            "/api/v1/agent/tasks/junta",
            json=[{"IDTask": str(task_id), "Code": "J-001", "ProgressPercentage": 25.5}],
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "analyzed",
        "count": 1,
        "tasks": [],
        "mergeGroups": [],
    }


def test_receive_junta_tasks_rejects_unknown_fields():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/v1/agent/tasks/junta",
        json=[{"IDTask": str(uuid4()), "unexpected": True}],
    )

    assert response.status_code == 422
