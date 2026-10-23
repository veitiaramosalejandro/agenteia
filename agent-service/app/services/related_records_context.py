"""Shared resolution of framework related records for every message path."""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.schema_query_planner import plan_related_record_query
from app.agent.tools import query_sql_server
from app.connectors.db_client import get_solidset_schema_snapshot
from app.connectors.solidset_sql import instance_context as solidset_sql_instance_context
from app.system.module_constants import ModuleConstants, resolve_record_module


def _value(node: Any, *names: str) -> Any:
    if not isinstance(node, dict):
        return None
    wanted = {name.casefold() for name in names}
    for key, value in node.items():
        if str(key).casefold() in wanted:
            return value
    return None


def _valid_reference(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text == "0" or text.casefold() == "00000000-0000-0000-0000-000000000000":
        return ""
    return text


def extract_related_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Read both payload.relatedRecords and chat.relatedRecords once, preserving order."""
    chat = _value(payload, "Chat", "chat")
    chat = chat if isinstance(chat, dict) else {}
    sources: list[Any] = []
    for value in (
        _value(chat, "relatedRecords", "RelatedRecords"),
        _value(payload, "RelatedRecordsData", "relatedRecordsData"),
    ):
        if isinstance(value, list):
            sources.extend(value)

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        module_id = str(_value(item, "idRecordModule", "IDRecordModule") or "").strip()
        module = resolve_record_module(module_id)
        record = {
            "idRecordModule": module["idRecordModule"] or module_id,
            "gidRecord": str(_value(item, "gidRecord", "GIDRecord") or "").strip(),
            "recordCode": str(_value(item, "recordCode", "RecordCode") or "").strip(),
            "recordShortName": str(_value(item, "recordShortName", "RecordShortName") or "").strip(),
            "recordTypeName": str(_value(item, "recordTypeName", "RecordTypeName") or "").strip(),
            "recordModuleName": module["moduleName"],
            "recordModuleKnown": module["known"],
        }
        key = (record["idRecordModule"].casefold(), record["gidRecord"].casefold(), record["recordCode"].casefold())
        if key in seen:
            continue
        seen.add(key)
        if any(record.values()):
            result.append(record)
    return result


def _sanitize(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split()).strip()
    text = re.sub(r"(?:solidset://)?file/[0-9a-f-]{16,}(?:[-_/][a-z0-9-]+)*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+([,.;:])", r"\1", text).replace("```", "").replace("`", "").strip(" -;,." )


def format_related_records_context(records: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for record in records[:10]:
        record_type = str(record.get("recordTypeName") or "Registro").strip()
        module_name = str(record.get("recordModuleName") or "").strip()
        label = " — ".join(
            value for value in (
                str(record.get("recordCode") or "").strip(),
                str(record.get("recordShortName") or "").strip(),
            ) if value
        )
        if module_name:
            record_type = f"{record_type} [{module_name}]"
        lines.append(f"{record_type}: {label or 'referencia asociada al turno'}")
    return "\n".join(lines)


_SYS_TASK_COLUMNS = (
    "IDTask", "IDResource", "IDModule", "IDResourceCreation", "IDResourceAssign",
    "Code", "Status", "DateCompletion", "Description", "importance", "StartDate",
    "EndDate", "Duration", "ShortName", "RunningStatus", "WorkStatus",
    "ProgressPercentage",
)


def _is_sys_tasks(record: dict[str, Any]) -> bool:
    return str(record.get("idRecordModule") or "").replace("-", "").upper() == ModuleConstants.SysTasks.replace("-", "").upper()


def verified_sys_tasks_context(instance: dict[str, Any], records: list[dict[str, Any]], requester_resource: str = "") -> str:
    task_records = [record for record in records if _is_sys_tasks(record)]
    ids = list(dict.fromkeys(
        _valid_reference(record.get("gidRecord")) for record in task_records[:10]
    ))
    ids = [value for value in ids if value]
    if not task_records:
        return ""
    if not ids:
        return "\n\n".join(format_related_records_context([record]) + "\nSQL_VERIFICATION: invalid task reference; client reference only." for record in task_records)
    query = "SELECT " + ", ".join(_SYS_TASK_COLUMNS) + " FROM dbo.SysTask WHERE IDTask IN (" + ", ".join("%s" for _ in ids) + ")"
    try:
        with solidset_sql_instance_context(instance):
            raw = query_sql_server.invoke({"query": query, "parameters_json": json.dumps(ids)})
        rows = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError, ValueError, RuntimeError) as exc:
        print(f"⚠️ No se pudieron ampliar tareas relacionadas: {exc}", flush=True)
        return "\n\n".join(format_related_records_context([record]) + "\nSQL_VERIFICATION: task query failed; client reference only." for record in task_records)
    by_id = {str(row.get("IDTask") or "").strip().casefold(): row for row in rows if isinstance(row, dict)} if isinstance(rows, list) else {}
    blocks: list[str] = []
    for record in task_records:
        row = by_id.get(str(record.get("gidRecord") or "").strip().casefold(), {})
        base = format_related_records_context([record])
        if not row:
            blocks.append(base + "\nSQL_VERIFICATION: no task rows; client reference only.")
            continue
        details = "\n".join(f"{key}: {_sanitize(row.get(key)) if row.get(key) not in (None, '') else 'NO DISPONIBLE'}" for key in _SYS_TASK_COLUMNS)
        blocks.append(base + "\nSQL_VERIFICATION: retrieved\n" + details)
        if requester_resource:
            from app.services.record_snapshot import schedule_record_snapshot
            schedule_record_snapshot(str(instance["ID"]), requester_resource, "SysTask", record, row)
    return "\n\n".join(blocks)


def verified_related_records_context(instance: dict[str, Any], records: list[dict[str, Any]], requester_resource: str = "") -> str:
    """Resolve all related records; SysTasks is authoritative and catalog-backed records remain supported."""
    if not records:
        return ""
    task_context = verified_sys_tasks_context(instance, records, requester_resource)
    generic_records = [record for record in records if not _is_sys_tasks(record)]
    if not generic_records:
        return task_context
    snapshot = get_solidset_schema_snapshot(instance.get("ID")) or {}
    catalog = snapshot.get("Catalog")
    if isinstance(catalog, str):
        catalog = json.loads(catalog)
    if not isinstance(catalog, dict):
        generic = format_related_records_context(generic_records) + "\nSQL_VERIFICATION: unavailable; client reference only."
        return "\n\n".join(value for value in (task_context, generic) if value)
    blocks: list[str] = []
    with solidset_sql_instance_context(instance):
        for record in generic_records[:10]:
            base = format_related_records_context([record])
            plan = plan_related_record_query(record, catalog)
            if plan is None:
                blocks.append(base + "\nSQL_VERIFICATION: unresolved record mapping; client reference only.")
                continue
            try:
                raw = query_sql_server.invoke({"query": plan.query, "parameters_json": json.dumps(plan.parameters)})
                rows = json.loads(str(raw))
            except (json.JSONDecodeError, TypeError, ValueError, RuntimeError) as exc:
                print(f"⚠️ No se pudo ampliar RelatedRecordsData: {exc}", flush=True)
                blocks.append(base + "\nSQL_VERIFICATION: query failed; client reference only.")
                continue
            row = rows[0] if isinstance(rows, list) and rows else {}
            details = "\n".join(f"{key}: {_sanitize(value) if value not in (None, '') else 'NO DISPONIBLE'}" for key, value in row.items())
            blocks.append(base + ("\nSQL_VERIFICATION: retrieved\n" + details if details else "\nSQL_VERIFICATION: no rows; client reference only."))
    generic = "\n\n".join(blocks)
    return "\n\n".join(value for value in (task_context, generic) if value)
