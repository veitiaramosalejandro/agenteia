"""Stable OpenAPI metadata for the modular HTTP API."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def configure_openapi(app: FastAPI, tags: list[dict[str, str]]) -> None:
    """Preserve the documented sections and descriptions across included routers."""
    descriptions = {item["name"]: item["description"] for item in tags}

    def build_schema() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=tags,
        )
        for path_item in schema.get("paths", {}).values():
            for method, operation in path_item.items():
                if method not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operation_tags = operation.get("tags") or []
                if not operation.get("description") and operation_tags:
                    operation["description"] = descriptions.get(operation_tags[0], "")
        app.openapi_schema = schema
        return schema

    app.openapi = build_schema
