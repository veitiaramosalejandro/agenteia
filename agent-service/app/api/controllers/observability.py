from __future__ import annotations

import csv
import io
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.connectors.db_client import (
    ensure_agent_automation_schema,
    ensure_agent_response_audit_schema,
    ensure_agent_tool_audit_schema,
    get_observability_snapshot,
    get_solidset_instance,
)
from app.services.dialogue_runtime import metrics_snapshot
from app.system.system_knowledge_ingest import ensure_system_knowledge_schema


router = APIRouter(tags=["Observability"])
EventType = Literal["response", "automation", "ingestion", "tool"]


def _snapshot(
    code: str,
    hours: int,
    limit: int,
    event_type: EventType | None,
    status: str | None,
    resource_id: UUID | None,
) -> dict:
    instance = get_solidset_instance(code=code.strip(), source_ip=None)
    if not instance:
        raise HTTPException(status_code=404, detail="A instância SolidSET não existe ou está inativa.")
    try:
        ensure_agent_response_audit_schema()
        ensure_agent_tool_audit_schema()
        ensure_agent_automation_schema()
        ensure_system_knowledge_schema()
        result = get_observability_snapshot(
            instance["ID"], hours=hours, limit=limit, event_type=event_type,
            status=status.strip() if status else None, resource_id=resource_id,
        )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível consultar a observabilidade.") from exc
    return {
        "instanceCode": instance["Code"],
        "instanceId": instance["ID"],
        "runtime": {"dialogue": metrics_snapshot()},
        **result,
    }


@router.get("/api/v1/agent/solidset/instances/{code}/observability")
def read_observability(
    code: str,
    hours: int = Query(24, ge=1, le=2160),
    limit: int = Query(200, ge=1, le=1000),
    eventType: EventType | None = None,
    status: str | None = Query(None, max_length=30),
    resourceId: UUID | None = None,
) -> dict:
    return _snapshot(code, hours, limit, eventType, status, resourceId)


def _csv_safe(value: object) -> str:
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text.replace("\x00", "")


@router.get("/api/v1/agent/solidset/instances/{code}/observability/export.csv")
def export_observability_csv(
    code: str,
    hours: int = Query(24, ge=1, le=2160),
    limit: int = Query(1000, ge=1, le=1000),
    eventType: EventType | None = None,
    status: str | None = Query(None, max_length=30),
    resourceId: UUID | None = None,
) -> StreamingResponse:
    snapshot = _snapshot(code, hours, limit, eventType, status, resourceId)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "type", "status", "duration_ms", "resource_id",
        "reference", "operation", "count", "error",
    ])
    for event in snapshot["events"]:
        writer.writerow([
            _csv_safe(event.get("timestamp")), _csv_safe(event.get("type")),
            _csv_safe(event.get("status")), _csv_safe(event.get("duration_ms")),
            _csv_safe(event.get("resourceId")), _csv_safe(event.get("reference")),
            _csv_safe(event.get("operation")), _csv_safe(event.get("count")),
            _csv_safe(event.get("error")),
        ])
    filename = f"agent-observability-{snapshot['instanceCode']}.csv"
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
