"""Shared resolution of framework related records for every message path."""

from __future__ import annotations

import json
import re
import unicodedata
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
    seen: dict[tuple[str, str, str], int] = {}
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
            "recordKind": str(
                _value(item, "recordKind", "RecordKind", "relatedRecordKind", "RelatedRecordKind") or ""
            ).strip(),
            "recordModuleName": module["moduleName"],
            "recordModuleKnown": module["known"],
        }
        key = (record["idRecordModule"].casefold(), record["gidRecord"].casefold(), record["recordCode"].casefold())
        if key in seen:
            current = result[seen[key]]
            for name, value in record.items():
                if not current.get(name) and value:
                    current[name] = value
            continue
        seen[key] = len(result)
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
        record_kind = str(record.get("recordKind") or "").strip()
        lines.append(f"{record_type}{f' (recordKind={record_kind})' if record_kind else ''}: {label or 'referencia asociada al turno'}")
    return "\n".join(lines)


def _payload_task(payload: dict[str, Any]) -> dict[str, Any]:
    chat = _value(payload, "Chat", "chat")
    chat = chat if isinstance(chat, dict) else {}
    for node in (
        _value(payload, "task", "Task"),
        _value(payload, "taskData", "TaskData"),
        _value(chat, "task", "Task"),
    ):
        if isinstance(node, dict) and any(node.values()):
            return node
    return {}


def _first_present(node: dict[str, Any], *names: str) -> Any:
    """Return the first present value without treating 0/False as missing."""
    for name in names:
        value = _value(node, name)
        if value is not None and (not isinstance(value, str) or value.strip()):
            return value
    return None


def _plain_text(value: Any) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()


def _available(value: Any) -> bool:
    text = str(value if value is not None else "").strip()
    return bool(text) and text.casefold() not in {
        "no disponible", "none", "null", "0001-01-01t00:00:00", "0001-01-01 00:00:00",
    }


def _display(value: Any, default: str = "no disponible") -> str:
    return _sanitize(value) if _available(value) else default


def build_related_record_prompt_context(
    payload: dict[str, Any], records: list[dict[str, Any]], verified_context: str,
) -> str:
    """Adds an explicit entity contract so the model cannot reinterpret "this task"."""
    task_records = [record for record in records if _is_sys_tasks(record)]
    if not task_records:
        return verified_context
    task = _payload_task(payload)
    first = task_records[0]
    code = str(task.get("code") or task.get("Code") or first.get("recordCode") or "").strip()
    title = str(
        task.get("shortName") or task.get("ShortName") or task.get("taskShortName")
        or first.get("recordShortName") or ""
    ).strip()
    fallback_fields = [
        ("Código", code),
        ("Título", title),
        ("Descripción", _first_present(task, "description", "Description")),
        ("Estado", _first_present(task, "statusStr", "StatusStr", "status")),
        ("Progreso", _first_present(task, "progressPercentage", "ProgressPercentage")),
        ("Inicio", _first_present(task, "start", "StartDate")),
        ("Fin", _first_present(task, "end", "EndDate", "dueDate")),
        ("Responsables", _first_present(task, "assignments")),
    ]
    fallback = "\n".join(f"{key}: {_sanitize(value)}" for key, value in fallback_fields if value not in (None, ""))
    context = verified_context.strip()
    if "SQL_VERIFICATION: retrieved" not in context and fallback:
        context = "\n\n".join(value for value in (context, "PAYLOAD_TASK_FALLBACK: verified framework payload\n" + fallback) if value)
    focus = (
        "\n\n=== CONTRATO OBLIGATORIO DEL REGISTRO ACTUAL ===\n"
        f"La pregunta se refiere a la tarea {code or title or first.get('gidRecord')}. "
        "Interpreta primero qué dato de esta tarea solicita el mensaje actual y responde ese dato usando únicamente los campos presentes en la ficha. "
        "No preguntes nuevamente cuál es la tarea ni solicites datos que ya aparecen en este contexto. "
        "No inventes pasos, causas, soluciones ni detalles que no estén sustentados por la ficha. "
        "Si solicitan una recomendación o solución y la ficha no contiene evidencia suficiente, explica brevemente qué sí indica la tarea y qué dato falta para proponer una solución fiable. "
        "Si un dato concreto no está disponible, dilo explícitamente."
    )
    return context + focus


def related_record_response_needs_repair(response: str, records: list[dict[str, Any]]) -> bool:
    """Detect generic clarification answers that ignored an authoritative task reference."""
    if not response or not any(_is_sys_tasks(record) for record in records):
        return False
    text = response.casefold()
    markers = (
        "necesito saber cuál es la tarea",
        "necesito saber que tarea",
        "necesito saber qué tarea",
        "cuál es la tarea concreta",
        "qué tarea concreta",
        "qual é a tarefa concreta",
        "qual tarefa concreta",
        "compartir más información sobre la tarea",
        "necesitaría saber un poco más sobre",
        "necesito saber un poco más sobre",
        "podrías describir brevemente cuál es la tarea",
        "podrias describir brevemente cual es la tarea",
        "necesito saber en qué consiste la tarea",
        "necesito saber en que consiste la tarea",
        "necesito saber cuál es el objetivo",
        "necesito saber cual es el objetivo",
        "qué tipo de tarea es",
        "que tipo de tarea es",
        "qual é a tarefa a que te referes",
        "qual é a tarefa a que se refere",
        "preciso de saber em que consiste a tarefa",
    )
    return any(marker in text for marker in markers)


_SYS_TASK_COLUMNS = (
    "IDTask", "IDResource", "IDModule", "IDResourceCreation", "IDResourceAssign",
    "Code", "Status", "DateCompletion", "Description", "importance", "StartDate",
    "EndDate", "Duration", "ShortName", "RunningStatus", "WorkStatus",
    "ProgressPercentage",
)


def _is_sys_tasks(record: dict[str, Any]) -> bool:
    return str(record.get("idRecordModule") or "").replace("-", "").upper() == ModuleConstants.SysTasks.replace("-", "").upper()


def resolve_sys_tasks_evidence(
    instance: dict[str, Any], records: list[dict[str, Any]], requester_resource: str = "",
) -> list[dict[str, Any]]:
    """Resolve SysTask references into structured evidence; never infer missing fields."""
    task_records = [record for record in records if _is_sys_tasks(record)]
    ids = list(dict.fromkeys(
        _valid_reference(record.get("gidRecord")) for record in task_records[:10]
    ))
    ids = [value for value in ids if value]
    if not task_records:
        return []
    if not ids:
        return [{"record": record, "row": {}, "verification": "invalid_reference"} for record in task_records]
    query = "SELECT " + ", ".join(_SYS_TASK_COLUMNS) + " FROM dbo.SysTask WHERE IDTask IN (" + ", ".join("%s" for _ in ids) + ")"
    try:
        with solidset_sql_instance_context(instance):
            raw = query_sql_server.invoke({"query": query, "parameters_json": json.dumps(ids)})
        rows = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError, ValueError, RuntimeError) as exc:
        print(f"⚠️ No se pudieron ampliar tareas relacionadas: {exc}", flush=True)
        return [{"record": record, "row": {}, "verification": "query_failed"} for record in task_records]
    by_id = {str(row.get("IDTask") or "").strip().casefold(): row for row in rows if isinstance(row, dict)} if isinstance(rows, list) else {}
    evidence: list[dict[str, Any]] = []
    for record in task_records:
        row = by_id.get(str(record.get("gidRecord") or "").strip().casefold(), {})
        if not row:
            evidence.append({"record": record, "row": {}, "verification": "not_found"})
            continue
        evidence.append({"record": record, "row": row, "verification": "retrieved"})
        if requester_resource:
            from app.services.record_snapshot import schedule_record_snapshot
            schedule_record_snapshot(str(instance["ID"]), requester_resource, "SysTask", record, row)
    return evidence


def format_sys_tasks_evidence(evidence: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    status_labels = {
        "invalid_reference": "invalid task reference; client reference only.",
        "query_failed": "task query failed; client reference only.",
        "not_found": "no task rows; client reference only.",
    }
    for item in evidence:
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        verification = str(item.get("verification") or "not_found")
        base = format_related_records_context([record])
        if verification != "retrieved":
            blocks.append(base + "\nSQL_VERIFICATION: " + status_labels.get(verification, "unavailable; client reference only."))
            continue
        details = "\n".join(
            f"{key}: {_sanitize(row.get(key)) if row.get(key) not in (None, '') else 'NO DISPONIBLE'}"
            for key in _SYS_TASK_COLUMNS
        )
        blocks.append(base + "\nSQL_VERIFICATION: retrieved\n" + details)
    return "\n\n".join(blocks)


def verified_sys_tasks_context(instance: dict[str, Any], records: list[dict[str, Any]], requester_resource: str = "") -> str:
    return format_sys_tasks_evidence(resolve_sys_tasks_evidence(instance, records, requester_resource))


def _payload_task_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    chat = _value(payload, "Chat", "chat")
    chat = chat if isinstance(chat, dict) else {}
    nodes = [
        node for node in (
            _value(payload, "task", "Task"),
            _value(payload, "taskData", "TaskData"),
            _value(chat, "task", "Task"),
            chat,
        )
        if isinstance(node, dict)
    ]
    return nodes


def _task_view(
    record: dict[str, Any], row: dict[str, Any], payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge one record with matching payload data; SQL remains authoritative."""
    record_id = str(record.get("gidRecord") or "").strip().casefold()
    record_code = str(record.get("recordCode") or "").strip().casefold()
    matching_nodes: list[dict[str, Any]] = []
    for node in _payload_task_nodes(payload):
        node_id = str(_first_present(node, "idTask", "IDTask") or "").strip().casefold()
        node_code = str(_first_present(node, "code", "Code", "taskTitle") or "").strip().casefold()
        if (record_id and node_id == record_id) or (record_code and node_code == record_code):
            matching_nodes.append(node)

    def field(row_name: str, *payload_names: str, record_name: str = "") -> Any:
        sql_value = row.get(row_name)
        if _available(sql_value):
            return sql_value
        for node in matching_nodes:
            value = _first_present(node, *payload_names)
            if _available(value) or value in (0, 0.0, False):
                return value
        return record.get(record_name) if record_name else None

    return {
        "id": field("IDTask", "idTask", "IDTask", record_name="gidRecord"),
        "code": field("Code", "code", "Code", "taskTitle", record_name="recordCode"),
        "title": field("ShortName", "shortName", "ShortName", "taskShortName", record_name="recordShortName"),
        "description": field("Description", "description", "Description"),
        "status": field("", "statusStr", "StatusStr") or field("Status", "status", "Status"),
        "running_status": field("", "runningStatusStr", "RunningStatusStr") or field("RunningStatus", "runningStatus", "RunningStatus"),
        "work_status": field("", "workStatusStr", "WorkStatusStr") or field("WorkStatus", "workStatus", "WorkStatus"),
        "progress": field("ProgressPercentage", "progressPercentage", "ProgressPercentage"),
        "start": field("StartDate", "start", "StartDate", "startDate"),
        "end": field("EndDate", "end", "EndDate", "endDate", "dueDate"),
        "completion": field("DateCompletion", "dateCompletion", "DateCompletion"),
        "duration": field("Duration", "duration", "Duration"),
        "importance": field("importance", "importance", "Importance"),
        "assigned": field("", "assignments", "Assignments") or field("IDResourceAssign", "idResourceAssign", "IDResourceAssign"),
        "record_type": record.get("recordTypeName"),
        "record_kind": record.get("recordKind"),
    }


def _task_reference(view: dict[str, Any]) -> str:
    code = _display(view.get("code"), "")
    title = _display(view.get("title"), "")
    if code and title:
        return f"{code} — {title}"
    return code or title or _display(view.get("id"), "la tarea relacionada")


def related_record_question_needs_guidance(
    user_text: str, records: list[dict[str, Any]],
) -> bool:
    """Whether the user asks for reasoning or a plan, not a stored task field."""
    if not any(_is_sys_tasks(record) for record in records):
        return False
    normalized = _plain_text(user_text)
    references_task = any(word in normalized for word in (
        "tarea", "tareas", "task", "tasks", "tarefa", "tarefas", "actividad", "atividade",
    ))
    references_record = any(
        _plain_text(value) in normalized
        for record in records
        if _is_sys_tasks(record)
        for value in (record.get("recordCode"), record.get("recordShortName"))
        if len(_plain_text(value)) >= 4
    )
    deictic = any(word in normalized.split() for word in (
        "esta", "estas", "este", "estos", "esto", "essa", "essas", "this", "these",
    ))
    asks_for_plan = any(word in normalized for word in (
        "resolver", "solucion", "como hago", "como hacer", "que hago", "recomend", "suger",
        "explicame como", "explica como", "resolve", "solution", "how to",
    ))
    return asks_for_plan and (references_task or references_record or deictic)


def answer_related_records_question(
    user_text: str,
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    task_evidence: list[dict[str, Any]],
    language: str = "es",
    allow_guidance_fallback: bool = False,
) -> str | None:
    """Answer task-reference questions from structured evidence before invoking an LLM."""
    task_records = [record for record in records if _is_sys_tasks(record)]
    if not task_records:
        return None
    normalized = _plain_text(user_text)
    references_task = any(word in normalized for word in (
        "tarea", "tareas", "task", "tasks", "tarefa", "tarefas", "actividad", "atividade",
    ))
    references_record = any(
        _plain_text(value) in normalized
        for record in task_records
        for value in (record.get("recordCode"), record.get("recordShortName"))
        if len(_plain_text(value)) >= 4
    )
    requested_fields = any(word in normalized for word in (
        "estado", "status", "progreso", "avance", "percent", "responsable", "asignad",
        "inicio", "fin", "fecha", "plazo", "descripcion", "detalle", "informacion",
        "resolver", "solucion", "recomend", "hacer", "como", "sabes", "know",
    ))
    deictic = any(word in normalized.split() for word in ("esta", "estas", "este", "estos", "esto", "essa", "essas", "this", "these"))
    if not (references_task or references_record or (deictic and requested_fields)):
        return None

    evidence_by_id = {
        str((item.get("record") or {}).get("gidRecord") or "").strip().casefold(): item
        for item in task_evidence if isinstance(item, dict)
    }
    views: list[dict[str, Any]] = []
    for record in task_records:
        item = evidence_by_id.get(str(record.get("gidRecord") or "").strip().casefold(), {})
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        view = _task_view(record, row, payload)
        view["verification"] = item.get("verification") or "payload_only"
        view["mentioned"] = any(
            _plain_text(value) in normalized
            for value in (record.get("recordCode"), record.get("recordShortName"))
            if len(_plain_text(value)) >= 4
        )
        views.append(view)

    if references_record:
        mentioned_views = [view for view in views if view.get("mentioned")]
        if mentioned_views:
            views = mentioned_views

    solution_intent = related_record_question_needs_guidance(user_text, records)
    status_intent = any(word in normalized for word in ("estado", "status", "situacion", "running"))
    progress_intent = any(word in normalized for word in ("progreso", "avance", "percent"))
    assigned_intent = any(word in normalized for word in ("responsable", "asignad", "quien", "quem"))
    dates_intent = any(word in normalized for word in ("inicio", "fin", "fecha", "plazo", "vence", "prazo", "deadline"))
    description_intent = any(word in normalized for word in ("descripcion", "consiste", "detalle", "sabes", "informacion", "sobre"))

    lang = language if language in {"es", "pt", "en"} else "es"
    if len(views) > 1 and not references_record:
        lines = []
        for view in views:
            facts = []
            if _available(view.get("status")):
                facts.append(f"estado {_display(view['status'])}")
            if view.get("progress") is not None and _available(view.get("progress")):
                facts.append(f"progreso {_display(view['progress'])}%")
            lines.append(f"- {_task_reference(view)}" + (f": {', '.join(facts)}" if facts else ": sin más datos verificados"))
        intro = {
            "es": "El mensaje contiene estas tareas relacionadas:",
            "pt": "A mensagem contém estas tarefas relacionadas:",
            "en": "The message contains these related tasks:",
        }[lang]
        return intro + "\n" + "\n".join(lines)

    view = views[0]
    reference = _task_reference(view)
    if solution_intent:
        # The task fields are evidence, but a "how do I solve it" request is
        # an analysis request. The assigned model must produce that analysis;
        # this factual text is only a last-resort safe fallback after a model
        # response was rejected for ignoring the referenced task.
        if not allow_guidance_fallback:
            return None
        description = _display(view.get("description"), "")
        status_bits = []
        if _available(view.get("status")):
            status_bits.append(f"estado {_display(view['status'])}")
        if _available(view.get("running_status")):
            status_bits.append(f"ejecución {_display(view['running_status'])}")
        if view.get("progress") is not None and _available(view.get("progress")):
            status_bits.append(f"progreso {_display(view['progress'])}%")
        state = ", ".join(status_bits) or "sin estado operativo verificado"
        if lang == "pt":
            if description:
                return f"A tarefa {reference} indica: {description}. Situação registada: {state}. A ficha não contém passos técnicos nem critérios de aceitação suficientes para afirmar uma implementação concreta sem inventar dados."
            return f"A tarefa {reference} está identificada e a situação registada é {state}, mas a ficha não contém descrição, requisitos técnicos nem critérios de aceitação. Por isso, ainda não há evidência suficiente para indicar como resolvê-la sem inventar informação."
        if lang == "en":
            if description:
                return f"Task {reference} states: {description}. Recorded state: {state}. The record does not contain enough technical steps or acceptance criteria to claim a concrete implementation without inventing details."
            return f"Task {reference} is identified and its recorded state is {state}, but the record has no description, technical requirements, or acceptance criteria. There is not enough evidence to say how to solve it without inventing information."
        if description:
            return f"La tarea {reference} indica: {description}. Situación registrada: {state}. La ficha no contiene pasos técnicos ni criterios de aceptación suficientes para afirmar una implementación concreta sin inventar datos."
        return f"La tarea {reference} está identificada y su situación registrada es {state}, pero la ficha no contiene descripción, requisitos técnicos ni criterios de aceptación. Por eso todavía no hay evidencia suficiente para indicar cómo resolverla sin inventar información."

    if status_intent:
        return f"La tarea {reference} tiene estado {_display(view.get('status'))}, estado de ejecución {_display(view.get('running_status'))} y estado de trabajo {_display(view.get('work_status'))}."
    if progress_intent:
        progress = _display(view.get("progress"))
        return f"El progreso registrado de la tarea {reference} es {progress}{'%' if progress != 'no disponible' else ''}."
    if assigned_intent:
        return f"El responsable o recurso asignado registrado para la tarea {reference} es {_display(view.get('assigned'))}."
    if dates_intent:
        return f"La tarea {reference} tiene inicio {_display(view.get('start'))}, fin {_display(view.get('end'))} y fecha de finalización {_display(view.get('completion'))}."
    if description_intent or references_task:
        facts = [
            f"estado {_display(view.get('status'))}",
            f"ejecución {_display(view.get('running_status'))}",
            f"progreso {_display(view.get('progress'))}{'%' if _available(view.get('progress')) else ''}",
        ]
        description = _display(view.get("description"), "")
        answer = f"La tarea relacionada es {reference}. Tiene {', '.join(facts)}."
        return answer + (f" Descripción: {description}." if description else " La ficha no contiene una descripción.")
    return None


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
