from app.main import OPENAPI_TAGS, app


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
FRAMEWORK_ENDPOINTS = {
    "/api/v1/agent/dialogue",
    "/api/v1/agent/notification/framework-message",
    "/api/v1/agent/notification/framework-message/preview",
    "/api/v1/agent/notification/chat-question/suggest-response",
}


def _operations(schema: dict):
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_openapi_contract_is_complete_and_grouped():
    app.openapi_schema = None
    schema = app.openapi()
    operations = list(_operations(schema))
    declared_tags = {item["name"] for item in OPENAPI_TAGS}

    assert len(schema["paths"]) == 50
    assert len(operations) == 54
    assert all(operation.get("tags") for _, _, operation in operations)
    assert all(operation.get("summary") for _, _, operation in operations)
    assert all(operation.get("description") for _, _, operation in operations)
    assert {
        tag for _, _, operation in operations for tag in operation["tags"]
    } <= declared_tags


def test_framework_operations_keep_request_examples():
    app.openapi_schema = None
    schema = app.openapi()

    for path in FRAMEWORK_ENDPOINTS:
        media_type = schema["paths"][path]["post"]["requestBody"]["content"][
            "application/json"
        ]
        assert len(media_type.get("examples") or {}) >= 2

