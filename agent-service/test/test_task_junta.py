from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.controllers.task_junta import router


def test_receive_junta_tasks_accepts_list():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    task_id = uuid4()

    response = client.post(
        "/api/v1/agent/tasks/junta",
        json=[{"IDTask": str(task_id), "Code": "J-001", "ProgressPercentage": 25.5}],
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "count": 1,
        "taskIds": [str(task_id)],
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
