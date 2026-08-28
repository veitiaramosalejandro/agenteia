from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


_CONCEPTS = {
    "company": {"empresa", "empresas", "companhia", "companhias", "company", "companies"},
    "organization": {"organización", "organizacion", "organização", "organizacao", "organization"},
    "resource": {"recurso", "recursos", "resource", "resources"},
    "community": {"comunidad", "comunidade", "community"},
}
_RELATION_TERMS = {
    "pertenezco", "pertenece", "pertenço", "pertence", "belong", "belongs",
    "asociado", "asociada", "associado", "associada", "associated",
}
_DISPLAY_COLUMNS = (
    "DisplayName", "FullName", "ShortName", "Name", "accountname", "Code",
    "Description", "Username", "ID",
)


@dataclass(frozen=True)
class SchemaQueryPlan:
    query: str
    parameters: list[str]
    concept: str
    anchor_table: str
    target_table: str
    selected_columns: tuple[str, ...]
    path: tuple[str, ...]


@dataclass(frozen=True)
class SchemaRecordPlan:
    query: str
    parameters: list[str]
    concept: str
    table: str
    selected_columns: tuple[str, ...]


def _words(text: str) -> set[str]:
    return {word.casefold() for word in re.findall(r"[A-Za-zÀ-ÿ0-9_]+", text or "")}


def _concept(text: str) -> Optional[str]:
    words = _words(text)
    for canonical, synonyms in _CONCEPTS.items():
        if words.intersection(synonyms):
            return canonical
    return None


def _column_name(column: dict[str, Any]) -> str:
    return str(column.get("name") or column.get("columnName") or "").strip()


def _valid_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


def plan_identity_relationship_query(
    user_text: str,
    catalog: dict[str, Any],
    *,
    login_id: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> Optional[SchemaQueryPlan]:
    """Planifica una relación identidad→entidad exclusivamente desde PK/FK reales."""
    plans = plan_identity_relationship_queries(
        user_text, catalog, login_id=login_id, resource_id=resource_id
    )
    return plans[0] if plans else None


def plan_identity_relationship_queries(
    user_text: str,
    catalog: dict[str, Any],
    *,
    login_id: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> tuple[SchemaQueryPlan, ...]:
    """Devuelve rutas FK candidatas ordenadas para permitir fallback sin LLM."""
    words = _words(user_text)
    concept = _concept(user_text)
    if not concept or not words.intersection(_RELATION_TERMS):
        return ()
    identities = (
        (("IDLogin",), str(login_id or "").strip()),
        (("IDResource", "ResourceId"), str(resource_id or "").strip()),
    )
    tables = {
        str(table.get("tableName") or "").casefold(): table
        for table in catalog.get("tables") or []
        if isinstance(table, dict) and table.get("tableName")
    }
    candidates: list[tuple[int, dict[str, Any], str, str]] = []
    for table in tables.values():
        table_name = str(table.get("tableName") or "")
        columns = {_column_name(column).casefold(): _column_name(column)
                   for column in table.get("columns") or [] if isinstance(column, dict)}
        for identity_columns, identity_value in identities:
            actual_identity_column = next(
                (columns.get(name.casefold()) for name in identity_columns if columns.get(name.casefold())),
                None,
            )
            if not identity_value or not actual_identity_column:
                continue
            score = 4 if concept in table_name.casefold() else 0
            score += sum(1 for name in columns if concept in name)
            if score:
                candidates.append((score, table, actual_identity_column, identity_value))
    if not candidates:
        return ()
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("tableName") or "")))
    plans: list[SchemaQueryPlan] = []
    for _, anchor, identity_column, identity_value in candidates:
        anchor_name = str(anchor.get("tableName") or "")
        foreign_keys = [fk for fk in anchor.get("foreignKeys") or [] if isinstance(fk, dict)]
        for target_fk in foreign_keys:
            if concept not in str(target_fk.get("column") or "").casefold():
                continue
            target_name = str(target_fk.get("referencedTable") or "").strip()
            target = tables.get(target_name.casefold())
            source_column = str(target_fk.get("column") or "").strip()
            target_column = str(target_fk.get("referencedColumn") or "").strip()
            if not target or not all(map(
                _valid_identifier,
                (anchor_name, target_name, source_column, target_column, identity_column),
            )):
                continue
            target_columns = {
                _column_name(column).casefold(): _column_name(column)
                for column in target.get("columns") or [] if isinstance(column, dict)
            }
            if target_column.casefold() not in target_columns:
                continue
            selected: list[str] = []
            for preferred in _DISPLAY_COLUMNS:
                actual = target_columns.get(preferred.casefold())
                if actual and actual not in selected:
                    selected.append(actual)
                if len(selected) >= 4:
                    break
            if not selected:
                continue
            select_sql = ", ".join(
                f"target.[{column}] AS [{column}]" for column in selected
            )
            query = (
                f"SELECT DISTINCT TOP 50 {select_sql} "
                f"FROM [dbo].[{anchor_name}] AS anchor "
                f"INNER JOIN [dbo].[{target_name}] AS target "
                f"ON anchor.[{source_column}] = target.[{target_column}] "
                f"WHERE anchor.[{identity_column}] = %s"
            )
            plans.append(SchemaQueryPlan(
                query=query, parameters=[identity_value], concept=concept,
                anchor_table=anchor_name, target_table=target_name,
                selected_columns=tuple(selected), path=(anchor_name, target_name),
            ))
    return tuple(plans)


def render_relationship_rows(
    rows: list[dict[str, Any]], plan: SchemaQueryPlan, language: str
) -> str:
    values: list[str] = []
    for row in rows:
        value = next(
            (str(row.get(column) or "").strip() for column in plan.selected_columns
             if str(row.get(column) or "").strip()),
            "",
        )
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)
    if not values:
        return {
            "pt": "Não encontrei uma relação ativa verificável para o seu utilizador.",
            "en": "I found no verifiable active relationship for your user.",
        }.get(language, "No encontré una relación activa verificable para tu usuario.")
    rendered = ", ".join(values)
    return {
        "pt": f"A empresa à qual você pertence no sistema é **{rendered}**.",
        "en": f"In the system, you belong to: **{rendered}**.",
    }.get(language, f"En el sistema perteneces a: **{rendered}**.")


def plan_identity_record_query(
    user_text: str,
    catalog: dict[str, Any],
    *,
    resource_id: Optional[str] = None,
) -> Optional[SchemaRecordPlan]:
    """Planifica registros actuales por semántica de columnas, sin nombres SQL fijos."""
    words = _words(user_text)
    task_terms = {"tarea", "tareas", "tarefa", "tarefas", "task", "tasks"}
    if not words.intersection(task_terms) or not resource_id:
        return None
    current_intent = bool(words.intersection({
        "actual", "atual", "current", "trabajando", "trabalhando", "trabalhar", "working",
    }))
    candidates: list[tuple[int, dict[str, Any], dict[str, str]]] = []
    for table in catalog.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("tableName") or "")
        columns = {
            _column_name(column).casefold(): _column_name(column)
            for column in table.get("columns") or [] if isinstance(column, dict)
        }
        identity_columns = [
            columns[name] for name in ("idresourceassign", "idresource", "resourceid")
            if name in columns
        ]
        if not identity_columns:
            continue
        score = 4 if "task" in table_name.casefold() else 0
        score += sum(1 for name in ("shortname", "workstatus", "progresspercentage") if name in columns)
        if score:
            candidates.append((score, table, columns))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("tableName") or "")))
    _, table, columns = candidates[0]
    table_name = str(table.get("tableName") or "")
    schema_name = str(table.get("schemaName") or "dbo")
    if not _valid_identifier(table_name) or not _valid_identifier(schema_name):
        return None
    identity_columns = [
        columns[name] for name in ("idresourceassign", "idresource", "resourceid")
        if name in columns and _valid_identifier(columns[name])
    ]
    preferred = (
        "ShortName", "Code", "WorkStatus", "RunningStatus", "Status",
        "ProgressPercentage", "StartDate", "EndDate", "ModifiedTime", "IDTask",
    )
    selected = tuple(
        columns[name.casefold()] for name in preferred if name.casefold() in columns
    )
    if not selected:
        return None
    where = ["(" + " OR ".join(f"src.[{name}] = %s" for name in identity_columns) + ")"]
    parameters = [str(resource_id)] * len(identity_columns)
    archived = columns.get("archived")
    if archived:
        where.append(f"ISNULL(src.[{archived}], 0) = 0")
    if current_intent:
        work_status = columns.get("workstatus")
        progress = columns.get("progresspercentage")
        if work_status:
            where.append(f"ISNULL(src.[{work_status}], 0) <> 0")
        if progress:
            where.append(f"ISNULL(src.[{progress}], 0) < 100")
    order_columns = [
        columns[name] for name in ("workstatus", "modifiedtime", "startdate") if name in columns
    ]
    order_sql = ", ".join(f"src.[{name}] DESC" for name in order_columns)
    query = (
        "SELECT TOP 10 "
        + ", ".join(f"src.[{name}] AS [{name}]" for name in selected)
        + f" FROM [{schema_name}].[{table_name}] AS src WHERE "
        + " AND ".join(where)
        + (f" ORDER BY {order_sql}" if order_sql else "")
    )
    return SchemaRecordPlan(
        query=query, parameters=parameters, concept="task", table=table_name,
        selected_columns=selected,
    )


def render_record_rows(rows: list[dict[str, Any]], plan: SchemaRecordPlan, language: str) -> str:
    if not rows:
        return {
            "pt": "Não encontrei nenhuma tarefa atual em execução para o seu recurso.",
            "en": "I found no current task in progress for your resource.",
        }.get(language, "No encontré ninguna tarea actual en ejecución para tu recurso.")
    row = rows[0]
    title = str(row.get("ShortName") or row.get("Code") or row.get("IDTask") or "").strip()
    progress = row.get("ProgressPercentage")
    suffix = f" (progresso: {progress}%)" if progress is not None and language == "pt" else ""
    if progress is not None and language == "en":
        suffix = f" (progress: {progress}%)"
    elif progress is not None and language not in {"pt", "en"}:
        suffix = f" (progreso: {progress}%)"
    return {
        "pt": f"A tarefa atual em que você está a trabalhar é **{title}**{suffix}.",
        "en": f"The current task you are working on is **{title}**{suffix}.",
    }.get(language, f"La tarea actual en la que estás trabajando es **{title}**{suffix}.")
