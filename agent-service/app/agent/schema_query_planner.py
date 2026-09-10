from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional


_CONCEPTS = {
    "company": {"empresa", "empresas", "companhia", "companhias", "company", "companies"},
    "organization": {"organización", "organizacion", "organização", "organizacao", "organization"},
    "resource": {"recurso", "recursos", "resource", "resources"},
    "community": {"comunidad", "comunidades", "comunidade", "comunidades", "community", "communities"},
}
_RELATION_TERMS = {
    "pertenezco", "pertenece", "pertenço", "pertence", "belong", "belongs",
    "asociado", "asociada", "associado", "associada", "associated",
}


def _has_relation_intent(words: set[str]) -> bool:
    """Reconoce flexiones abiertas sin enumerar cada conjugación."""
    stems = ("pertenec", "pertenc", "belong", "asocia", "associa", "associat")
    return bool(words.intersection(_RELATION_TERMS)) or any(
        word.startswith(stem) for word in words for stem in stems
    )
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
    temporal_scope: str = "latest"
    response_mode: str = "single"


def plan_related_record_query(
    record: dict[str, Any], catalog: dict[str, Any]
) -> Optional[SchemaRecordPlan]:
    """Resuelve un RelatedRecordsData contra el catálogo sin asumir una tabla fija."""
    record_type = str(record.get("recordTypeName") or "").strip()
    gid = str(record.get("gidRecord") or "").strip()
    try:
        gid = str(uuid.UUID(gid)) if gid else ""
    except (ValueError, AttributeError):
        gid = ""
    code = str(record.get("recordCode") or "").strip()
    type_words = _words(record_type)
    if not type_words or not (gid or code):
        return None

    candidates: list[tuple[int, dict[str, Any], dict[str, str]]] = []
    for table in catalog.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("tableName") or "")
        columns = {
            _column_name(column).casefold(): _column_name(column)
            for column in table.get("columns") or [] if isinstance(column, dict)
        }
        table_words = _words(table_name.replace("Sys", " "))
        semantic_match = any(
            word in table_name.casefold() or word in table_words for word in type_words
        )
        id_names = [f"id{word}".casefold() for word in type_words] + ["id"]
        has_anchor = (code and "code" in columns) or (
            gid and any(name in columns for name in id_names)
        )
        if semantic_match and has_anchor:
            score = 5 + sum(1 for name in ("shortname", "description", "technicalspecification") if name in columns)
            candidates.append((score, table, columns))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("tableName") or "")))
    _, table, columns = candidates[0]
    table_name = str(table.get("tableName") or "")
    schema_name = str(table.get("schemaName") or "dbo")
    if not _valid_identifier(table_name) or not _valid_identifier(schema_name):
        return None

    preferred = (
        "Code", "ShortName", "Description", "TechnicalSpecification", "Status",
        "WorkStatus", "ProgressPercentage", "StartDate", "EndDate", "DueDate",
        "Priority", "Complexity", "ModifiedTime",
        "DateCompletion", "Duration", "DurationEstimated", "TotalWorkDuration",
        "StartDateEstimated", "EndDateEstimated",
    )
    selected = tuple(columns[name.casefold()] for name in preferred if name.casefold() in columns)
    predicates: list[str] = []
    parameters: list[str] = []
    if code and "code" in columns:
        predicates.append(f"src.[{columns['code']}] = %s")
        parameters.append(code)
    for name in [f"id{word}".casefold() for word in type_words] + ["id"]:
        if gid and name in columns:
            predicates.append(f"src.[{columns[name]}] = %s")
            parameters.append(gid)
            break
    if not selected or not predicates:
        return None
    query = (
        "SELECT TOP 1 "
        + ", ".join(f"src.[{name}] AS [{name}]" for name in selected)
        + f" FROM [{schema_name}].[{table_name}] AS src WHERE ("
        + " OR ".join(predicates)
        + ")"
    )
    return SchemaRecordPlan(
        query=query, parameters=parameters, concept=f"related:{record_type}",
        table=table_name, selected_columns=selected,
    )


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
    if not concept or not _has_relation_intent(words):
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
    if concept == "company" and resource_id:
        community_company = _plan_resource_company_via_community(
            tables, str(resource_id)
        )
        if community_company is not None:
            plans.append(community_company)
    return tuple(plans)


def _plan_resource_company_via_community(
    tables: dict[str, dict[str, Any]], resource_id: str,
) -> Optional[SchemaQueryPlan]:
    """Planifica recurso→comunidad→empresa validando cada FK del catálogo."""
    required = {
        name: tables.get(name.casefold())
        for name in (
            "SysCommunity2Resource", "SysCommunity",
            "SysCommunity2Company", "Entity",
        )
    }
    if any(table is None for table in required.values()):
        return None

    def columns(table: dict[str, Any]) -> dict[str, str]:
        return {
            _column_name(column).casefold(): _column_name(column)
            for column in table.get("columns") or [] if isinstance(column, dict)
        }

    def has_fk(table: dict[str, Any], column: str, target: str, target_column: str) -> bool:
        return any(
            str(fk.get("column") or "").casefold() == column.casefold()
            and str(fk.get("referencedTable") or "").casefold() == target.casefold()
            and str(fk.get("referencedColumn") or "").casefold() == target_column.casefold()
            for fk in table.get("foreignKeys") or [] if isinstance(fk, dict)
        )

    resource_link = required["SysCommunity2Resource"]
    community = required["SysCommunity"]
    company_link = required["SysCommunity2Company"]
    entity = required["Entity"]
    resource_columns = columns(resource_link)
    community_columns = columns(community)
    company_columns = columns(company_link)
    entity_columns = columns(entity)
    required_columns = (
        resource_columns.get("idresource"), resource_columns.get("idcommunity"),
        community_columns.get("id"), company_columns.get("idcommunity"),
        company_columns.get("idcompany"), entity_columns.get("id"),
    )
    if not all(required_columns):
        return None
    if not (
        has_fk(resource_link, resource_columns["idcommunity"], "SysCommunity", community_columns["id"])
        and has_fk(company_link, company_columns["idcommunity"], "SysCommunity", community_columns["id"])
        and has_fk(company_link, company_columns["idcompany"], "Entity", entity_columns["id"])
    ):
        return None
    selected = tuple(
        entity_columns[name.casefold()]
        for name in _DISPLAY_COLUMNS
        if name.casefold() in entity_columns
    )[:4]
    if not selected:
        return None
    select_sql = ", ".join(f"target.[{name}] AS [{name}]" for name in selected)
    query = (
        f"SELECT DISTINCT TOP 50 {select_sql} "
        "FROM [dbo].[SysCommunity2Resource] AS membership "
        "INNER JOIN [dbo].[SysCommunity] AS community "
        f"ON membership.[{resource_columns['idcommunity']}] = community.[{community_columns['id']}] "
        "INNER JOIN [dbo].[SysCommunity2Company] AS company_membership "
        f"ON company_membership.[{company_columns['idcommunity']}] = community.[{community_columns['id']}] "
        "INNER JOIN [dbo].[Entity] AS target "
        f"ON company_membership.[{company_columns['idcompany']}] = target.[{entity_columns['id']}] "
        f"WHERE membership.[{resource_columns['idresource']}] = %s"
    )
    return SchemaQueryPlan(
        query=query, parameters=[resource_id], concept="company",
        anchor_table="SysCommunity2Resource", target_table="Entity",
        selected_columns=selected,
        path=("SysCommunity2Resource", "SysCommunity", "SysCommunity2Company", "Entity"),
    )


def render_relationship_rows(
    rows: list[dict[str, Any]], plan: SchemaQueryPlan, language: str,
    *, perspective: str = "requester", subject_label: str = "",
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
    if perspective == "agent":
        return {
            "pt": f"No sistema, pertenço a **{rendered}**.",
            "en": f"In the system, I belong to **{rendered}**.",
        }.get(language, f"En el sistema pertenezco a **{rendered}**.")
    if perspective == "third_party":
        owner = subject_label or {"pt": "Este recurso", "en": "This resource"}.get(language, "Este recurso")
        return {
            "pt": f"No sistema, {owner} pertence a **{rendered}**.",
            "en": f"In the system, {owner} belongs to **{rendered}**.",
        }.get(language, f"En el sistema, {owner} pertenece a **{rendered}**.")
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
    activity_terms = {"actividad", "actividades", "atividade", "atividades", "activity", "activities"}
    if words.intersection(activity_terms) and resource_id:
        return _plan_resource_activity_query(user_text, catalog, str(resource_id))
    if not words.intersection(task_terms) or not resource_id:
        return None
    current_intent = bool(words.intersection({
        "actual", "atual", "current", "trabajando", "trabalhando", "trabalhar", "working",
    }))
    summary_intent = bool(words.intersection({
        "resumen", "resumo", "summary", "estado", "status", "cumplimiento",
        "cumprimento", "progreso", "progresso", "progress",
    }))
    overdue_intent = bool(words.intersection({
        "incumpli", "incumpliste", "incumplida", "incumplidas", "vencida", "vencidas",
        "atrasada", "atrasadas", "overdue", "late",
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
    identity_columns = list(dict.fromkeys(
        actual for normalized, actual in columns.items()
        if (normalized == "resourceid" or normalized.startswith("idresource"))
        and _valid_identifier(actual)
    ))
    preferred = (
        "ShortName", "Code", "WorkStatus", "RunningStatus", "Status",
        "ProgressPercentage", "StartDate", "EndDate", "ModifiedTime", "IDTask",
    )
    selected = tuple(
        columns[name.casefold()] for name in preferred if name.casefold() in columns
    )
    if not selected:
        return None
    resource_predicates = [f"src.[{name}] = %s" for name in identity_columns]
    parameters = [str(resource_id)] * len(identity_columns)

    # Las asignaciones de una entidad pueden estar normalizadas en tablas de
    # relación. Se descubren desde el catálogo y se incorporan al mismo filtro
    # de identidad; nunca se recuperan filas globales para filtrarlas después.
    task_key = columns.get("idtask")
    if task_key:
        relation_candidates: list[tuple[int, dict[str, Any], dict[str, str]]] = []
        for relation_table in catalog.get("tables") or []:
            if not isinstance(relation_table, dict) or relation_table is table:
                continue
            relation_name = str(relation_table.get("tableName") or "")
            relation_columns = {
                _column_name(column).casefold(): _column_name(column)
                for column in relation_table.get("columns") or [] if isinstance(column, dict)
            }
            if "idtask" not in relation_columns:
                continue
            relation_identity = next((
                actual for normalized, actual in relation_columns.items()
                if normalized == "resourceid" or normalized.startswith("idresource")
            ), None)
            if not relation_identity:
                continue
            foreign_keys = [
                fk for fk in relation_table.get("foreignKeys") or []
                if isinstance(fk, dict)
            ]
            task_fk_verified = any(
                str(fk.get("column") or "").casefold() == "idtask"
                and str(fk.get("referencedTable") or "").casefold() == table_name.casefold()
                and str(fk.get("referencedColumn") or "").casefold() == task_key.casefold()
                for fk in foreign_keys
            )
            resource_fk_verified = any(
                str(fk.get("column") or "").casefold() == relation_identity.casefold()
                and str(fk.get("referencedTable") or "").casefold() == "sysresources"
                for fk in foreign_keys
            )
            if not (task_fk_verified and resource_fk_verified):
                continue
            score = (4 if "task" in relation_name.casefold() else 0) + (
                2 if "role" in relation_name.casefold() else 0
            )
            relation_candidates.append((score, relation_table, relation_columns))
        relation_candidates.sort(
            key=lambda item: (-item[0], str(item[1].get("tableName") or ""))
        )
        for index, (_score, relation_table, relation_columns) in enumerate(relation_candidates):
            relation_name = str(relation_table.get("tableName") or "")
            relation_schema = str(relation_table.get("schemaName") or "dbo")
            relation_resource = next(
                actual for normalized, actual in relation_columns.items()
                if normalized == "resourceid" or normalized.startswith("idresource")
            )
            relation_task = relation_columns["idtask"]
            if not all(_valid_identifier(value) for value in (
                relation_name, relation_schema, relation_resource, relation_task,
            )):
                continue
            alias = f"rel{index}"
            active_clause = ""
            if "linkstate" in relation_columns:
                active_clause = f" AND ISNULL({alias}.[{relation_columns['linkstate']}], 1) <> 0"
            resource_predicates.append(
                f"EXISTS (SELECT 1 FROM [{relation_schema}].[{relation_name}] AS {alias} "
                f"WHERE {alias}.[{relation_task}] = src.[{task_key}] "
                f"AND {alias}.[{relation_resource}] = %s{active_clause})"
            )
            parameters.append(str(resource_id))

    if not resource_predicates:
        return None
    where = ["(" + " OR ".join(resource_predicates) + ")"]
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
    order_names = (
        ("workstatus", "modifiedtime", "startdate")
        if current_intent else ("modifiedtime", "startdate")
    )
    order_columns = [columns[name] for name in order_names if name in columns]
    order_sql = ", ".join(f"src.[{name}] DESC" for name in order_columns)
    if overdue_intent and columns.get("duedate"):
        due_date = columns["duedate"]
        progress = columns.get("progresspercentage")
        overdue_where = list(where)
        overdue_where.append(f"src.[{due_date}] < GETDATE()")
        if progress:
            overdue_where.append(f"ISNULL(src.[{progress}], 0) < 100")
        query = (
            f"SELECT COUNT(*) AS [OverdueCount] FROM [{schema_name}].[{table_name}] AS src WHERE "
            + " AND ".join(overdue_where)
        )
        selected = ("OverdueCount",)
    elif summary_intent:
        aggregates = ["COUNT(*) AS [TaskCount]"]
        progress = columns.get("progresspercentage")
        if progress:
            aggregates.extend((
                f"SUM(CASE WHEN src.[{progress}] >= 100 THEN 1 ELSE 0 END) AS [CompletedCount]",
                f"SUM(CASE WHEN src.[{progress}] > 0 AND src.[{progress}] < 100 THEN 1 ELSE 0 END) AS [InProgressCount]",
                f"SUM(CASE WHEN src.[{progress}] IS NULL OR src.[{progress}] <= 0 THEN 1 ELSE 0 END) AS [NotStartedCount]",
                f"AVG(CAST(src.[{progress}] AS decimal(18,2))) AS [AverageProgress]",
            ))
        work_status = columns.get("workstatus")
        if work_status:
            aggregates.append(
                f"SUM(CASE WHEN ISNULL(src.[{work_status}], 0) <> 0 THEN 1 ELSE 0 END) AS [ActiveCount]"
            )
        query = (
            "SELECT " + ", ".join(aggregates)
            + f" FROM [{schema_name}].[{table_name}] AS src WHERE "
            + " AND ".join(where)
        )
        selected = tuple(
            name for name in (
                "TaskCount", "CompletedCount", "InProgressCount", "NotStartedCount",
                "AverageProgress", "ActiveCount",
            )
            if any(f"[{name}]" in aggregate for aggregate in aggregates)
        )
    else:
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
        temporal_scope="current" if current_intent else "latest",
        response_mode=("overdue_count" if overdue_intent and columns.get("duedate")
                       else "summary" if summary_intent else "single"),
    )


def _plan_resource_activity_query(
    user_text: str, catalog: dict[str, Any], resource_id: str,
) -> Optional[SchemaRecordPlan]:
    """Planifica actividades mediante columnas y relaciones FK verificadas."""
    table = next((
        value for value in catalog.get("tables") or []
        if isinstance(value, dict)
        and str(value.get("tableName") or "").casefold() == "activity"
    ), None)
    if not table:
        return None
    table_name = str(table.get("tableName") or "")
    schema_name = str(table.get("schemaName") or "dbo")
    columns = {
        _column_name(column).casefold(): _column_name(column)
        for column in table.get("columns") or [] if isinstance(column, dict)
    }
    activity_key = columns.get("idactivity")
    if not activity_key or not all(_valid_identifier(v) for v in (table_name, schema_name, activity_key)):
        return None

    direct_resource_columns = list(dict.fromkeys(
        actual for normalized, actual in columns.items()
        if (normalized == "resourceid" or normalized.startswith("idresource"))
        and _valid_identifier(actual)
    ))
    predicates = [f"src.[{column}] = %s" for column in direct_resource_columns]
    parameters = [resource_id] * len(direct_resource_columns)

    for index, relation in enumerate(catalog.get("tables") or []):
        if not isinstance(relation, dict):
            continue
        relation_columns = {
            _column_name(column).casefold(): _column_name(column)
            for column in relation.get("columns") or [] if isinstance(column, dict)
        }
        relation_resource = next((
            actual for normalized, actual in relation_columns.items()
            if normalized == "resourceid" or normalized.startswith("idresource")
        ), None)
        relation_activity = relation_columns.get("idactivity")
        if not relation_resource or not relation_activity:
            continue
        foreign_keys = [fk for fk in relation.get("foreignKeys") or [] if isinstance(fk, dict)]
        links_activity = any(
            str(fk.get("column") or "").casefold() == relation_activity.casefold()
            and str(fk.get("referencedTable") or "").casefold() == table_name.casefold()
            and str(fk.get("referencedColumn") or "").casefold() == activity_key.casefold()
            for fk in foreign_keys
        )
        links_resource = any(
            str(fk.get("column") or "").casefold() == relation_resource.casefold()
            and str(fk.get("referencedTable") or "").casefold() == "sysresources"
            for fk in foreign_keys
        )
        if not (links_activity and links_resource):
            continue
        relation_name = str(relation.get("tableName") or "")
        relation_schema = str(relation.get("schemaName") or "dbo")
        if not all(_valid_identifier(v) for v in (
            relation_name, relation_schema, relation_resource, relation_activity,
        )):
            continue
        alias = f"rel{index}"
        active_clauses = []
        if "linkstate" in relation_columns:
            active_clauses.append(f"ISNULL({alias}.[{relation_columns['linkstate']}], 1) <> 0")
        if "participationactive" in relation_columns:
            active_clauses.append(
                f"ISNULL({alias}.[{relation_columns['participationactive']}], 1) <> 0"
            )
        suffix = "" if not active_clauses else " AND " + " AND ".join(active_clauses)
        predicates.append(
            f"EXISTS (SELECT 1 FROM [{relation_schema}].[{relation_name}] AS {alias} "
            f"WHERE {alias}.[{relation_activity}] = src.[{activity_key}] "
            f"AND {alias}.[{relation_resource}] = %s{suffix})"
        )
        parameters.append(resource_id)
    if not predicates:
        return None

    where = ["(" + " OR ".join(predicates) + ")"]
    words = _words(user_text)
    pending = bool(words.intersection({
        "pendiente", "pendientes", "pendente", "pendentes", "pending", "open",
    }))
    if pending and "iscomplete" in columns:
        where.append(f"ISNULL(src.[{columns['iscomplete']}], 0) = 0")
    selected_names = (
        "subject", "activitycode", "statusdescription", "status", "iscomplete",
        "priority", "startdate", "enddate", "modifiedtime", "idactivity",
    )
    selected = tuple(columns[name] for name in selected_names if name in columns)
    if not selected:
        return None
    order_names = ("enddate", "modifiedtime", "startdate") if pending else (
        "modifiedtime", "startdate",
    )
    order_columns = [columns[name] for name in order_names if name in columns]
    query = (
        "SELECT TOP 15 "
        + ", ".join(f"src.[{name}] AS [{name}]" for name in selected)
        + f" FROM [{schema_name}].[{table_name}] AS src WHERE "
        + " AND ".join(where)
        + (" ORDER BY " + ", ".join(f"src.[{name}] DESC" for name in order_columns)
           if order_columns else "")
    )
    return SchemaRecordPlan(
        query=query,
        parameters=parameters,
        concept="activity",
        table=table_name,
        selected_columns=selected,
        temporal_scope="current" if pending else "latest",
        response_mode="list",
    )


def render_record_rows(
    rows: list[dict[str, Any]],
    plan: SchemaRecordPlan,
    language: str,
    perspective: str = "neutral",
    subject_label: str = "",
) -> str:
    if not rows:
        if plan.temporal_scope == "latest":
            return {
                "pt": "Não encontrei nenhuma tarefa verificável relacionada com este recurso.",
                "en": "I found no verifiable task related to this resource.",
            }.get(language, "No encontré ninguna tarea verificable relacionada con este recurso.")
        return {
            "pt": "Não encontrei nenhuma tarefa atual em execução para o seu recurso.",
            "en": "I found no current task in progress for your resource.",
        }.get(language, "No encontré ninguna tarea actual en ejecución para tu recurso.")
    row = rows[0]
    if plan.response_mode == "list":
        rendered = []
        for item in rows:
            title = str(
                item.get("subject") or item.get("Subject")
                or item.get("activityCode") or item.get("ActivityCode")
                or item.get("IDActivity") or "Actividad sin título"
            ).strip()
            status = item.get("StatusDescription") or item.get("statusDescription")
            end_date = item.get("endDate") or item.get("EndDate")
            details = []
            if status not in (None, ""):
                details.append(f"estado: {status}")
            if end_date not in (None, ""):
                details.append(f"fin: {end_date}")
            rendered.append(f"- **{title}**" + (f" ({'; '.join(details)})" if details else ""))
        if perspective == "agent":
            heading = {
                "pt": f"Tenho **{len(rows)} atividades verificadas** pendentes:",
                "en": f"I have **{len(rows)} verified pending activities**:",
            }.get(language, f"Tengo **{len(rows)} actividades pendientes verificadas**:")
        elif perspective == "requester":
            heading = {
                "pt": f"Tem **{len(rows)} atividades verificadas** pendentes:",
                "en": f"You have **{len(rows)} verified pending activities**:",
            }.get(language, f"Tienes **{len(rows)} actividades pendientes verificadas**:")
        else:
            owner = subject_label or {
                "pt": "Este recurso", "en": "This resource"
            }.get(language, "Este recurso")
            heading = {
                "pt": f"{owner} tem **{len(rows)} atividades verificadas** pendentes:",
                "en": f"{owner} has **{len(rows)} verified pending activities**:",
            }.get(language, f"{owner} tiene **{len(rows)} actividades pendientes verificadas**:")
        return heading + "\n" + "\n".join(rendered)
    if plan.response_mode == "overdue_count":
        count = int(row.get("OverdueCount") or 0)
        es_tasks = "tarea vencida" if count == 1 else "tareas vencidas"
        pt_tasks = "tarefa vencida" if count == 1 else "tarefas vencidas"
        en_tasks = "overdue task" if count == 1 else "overdue tasks"
        if perspective == "agent":
            return {
                "pt": f"Tenho **{count} {pt_tasks} que ainda está abaixo de 100% de progresso**." if count == 1 else f"Tenho **{count} {pt_tasks} que ainda estão abaixo de 100% de progresso**.",
                "en": f"I have **{count} {en_tasks} still below 100% progress**.",
            }.get(language, f"Tengo **{count} {es_tasks} que todavía {'está' if count == 1 else 'están'} por debajo del 100% de progreso**.")
        if perspective == "requester":
            return {
                "pt": f"Tem **{count} {pt_tasks} ainda abaixo de 100% de progresso**.",
                "en": f"You have **{count} {en_tasks} still below 100% progress**.",
            }.get(language, f"Tienes **{count} {es_tasks} todavía por debajo del 100% de progreso**.")
        if not subject_label:
            return {
                "pt": f"Encontrei **{count} {pt_tasks} ainda abaixo de 100% de progresso** relacionada com este recurso." if count == 1 else f"Encontrei **{count} {pt_tasks} ainda abaixo de 100% de progresso** relacionadas com este recurso.",
                "en": f"I found **{count} {en_tasks} still below 100% progress** related to this resource.",
            }.get(language, f"Encontré **{count} {es_tasks} todavía por debajo del 100% de progreso** {'relacionada' if count == 1 else 'relacionadas'} con este recurso.")
        owner = subject_label
        return {
            "pt": f"{owner} tem **{count} {pt_tasks} ainda abaixo de 100% de progresso**.",
            "en": f"{owner} has **{count} {en_tasks} still below 100% progress**.",
        }.get(language, f"{owner} tiene **{count} {es_tasks} todavía por debajo del 100% de progreso**.")
    if plan.response_mode == "summary":
        total = int(row.get("TaskCount") or 0)
        completed = int(row.get("CompletedCount") or 0)
        in_progress = int(row.get("InProgressCount") or 0)
        not_started = int(row.get("NotStartedCount") or 0)
        active = int(row.get("ActiveCount") or 0)
        average = row.get("AverageProgress")
        average_text = f"{float(average):.2f}%" if average is not None else "sin dato"
        completion_rate = (completed * 100 / total) if total else 0.0
        if perspective == "agent":
            return {
                "pt": (
                    f"Tenho **{total} tarefas**: **{completed} concluídas a 100%**, "
                    f"**{in_progress} em progresso** e **{not_started} sem progresso registado**. "
                    f"O meu progresso médio é **{average_text}** e o cumprimento integral é **{completion_rate:.2f}%**."
                ),
                "en": (
                    f"I have **{total} tasks**: **{completed} completed at 100%**, "
                    f"**{in_progress} in progress**, and **{not_started} with no recorded progress**. "
                    f"My average progress is **{average_text}** and my full completion rate is **{completion_rate:.2f}%**."
                ),
            }.get(language, (
                f"Tengo **{total} tareas**: **{completed} completadas al 100%**, "
                f"**{in_progress} en progreso** y **{not_started} sin progreso registrado**. "
                f"Mi progreso medio es **{average_text}** y mi cumplimiento total es **{completion_rate:.2f}%**."
            ))
        if perspective == "requester":
            return {
                "pt": (
                    f"Tem **{total} tarefas**: **{completed} concluídas a 100%**, "
                    f"**{in_progress} em progresso** e **{not_started} sem progresso registado**. "
                    f"O seu progresso médio é **{average_text}** e o cumprimento integral é **{completion_rate:.2f}%**."
                ),
                "en": (
                    f"You have **{total} tasks**: **{completed} completed at 100%**, "
                    f"**{in_progress} in progress**, and **{not_started} with no recorded progress**. "
                    f"Your average progress is **{average_text}** and your full completion rate is **{completion_rate:.2f}%**."
                ),
            }.get(language, (
                f"Tienes **{total} tareas**: **{completed} completadas al 100%**, "
                f"**{in_progress} en progreso** y **{not_started} sin progreso registrado**. "
                f"Tu progreso medio es **{average_text}** y tu cumplimiento total es **{completion_rate:.2f}%**."
            ))
        if not subject_label:
            return {
                "pt": (
                    f"Resumo verificado das tarefas relacionadas com este recurso: **{total} tarefas**; "
                    f"**{completed} concluídas a 100%**, **{in_progress} em progresso**, "
                    f"**{not_started} sem progresso registado** e **{active} com estado de trabalho ativo**. "
                    f"Progresso médio: **{average_text}**; cumprimento integral: **{completion_rate:.2f}%**."
                ),
                "en": (
                    f"Verified summary of tasks related to this resource: **{total} tasks**; "
                    f"**{completed} completed at 100%**, **{in_progress} in progress**, "
                    f"**{not_started} with no recorded progress**, and **{active} with active work status**. "
                    f"Average progress: **{average_text}**; full completion rate: **{completion_rate:.2f}%**."
                ),
            }.get(language, (
                f"Resumen verificado de las tareas relacionadas con este recurso: **{total} tareas**; "
                f"**{completed} completadas al 100%**, **{in_progress} en progreso**, "
                f"**{not_started} sin progreso registrado** y **{active} con estado de trabajo activo**. "
                f"Progreso medio: **{average_text}**; cumplimiento total: **{completion_rate:.2f}%**."
            ))
        owner = subject_label
        return {
            "pt": (
                f"{owner} tem **{total} tarefas**; "
                f"**{completed} concluídas a 100%**, **{in_progress} em progresso**, "
                f"**{not_started} sem progresso registado** e **{active} com estado de trabalho ativo**. "
                f"Progresso médio: **{average_text}**; cumprimento integral: **{completion_rate:.2f}%**."
            ),
            "en": (
                f"{owner} has **{total} tasks**; "
                f"**{completed} completed at 100%**, **{in_progress} in progress**, "
                f"**{not_started} with no recorded progress**, and **{active} with active work status**. "
                f"Average progress: **{average_text}**; full completion rate: **{completion_rate:.2f}%**."
            ),
        }.get(language, (
            f"{owner} tiene **{total} tareas**; "
            f"**{completed} completadas al 100%**, **{in_progress} en progreso**, "
            f"**{not_started} sin progreso registrado** y **{active} con estado de trabajo activo**. "
            f"Progreso medio: **{average_text}**; cumplimiento total: **{completion_rate:.2f}%**."
        ))
    title = str(row.get("ShortName") or row.get("Code") or row.get("IDTask") or "").strip()
    progress = row.get("ProgressPercentage")
    suffix = f" (progresso: {progress}%)" if progress is not None and language == "pt" else ""
    if progress is not None and language == "en":
        suffix = f" (progress: {progress}%)"
    elif progress is not None and language not in {"pt", "en"}:
        suffix = f" (progreso: {progress}%)"
    if plan.temporal_scope == "latest":
        if perspective == "agent":
            return {
                "pt": f"A minha tarefa mais recente é **{title}**{suffix}.",
                "en": f"My latest task is **{title}**{suffix}.",
            }.get(language, f"Mi última tarea es **{title}**{suffix}.")
        if perspective == "requester":
            return {
                "pt": f"A sua tarefa mais recente é **{title}**{suffix}.",
                "en": f"Your latest task is **{title}**{suffix}.",
            }.get(language, f"Tu última tarea es **{title}**{suffix}.")
        if not subject_label:
            return {
                "pt": f"A tarefa mais recente relacionada com este recurso é **{title}**{suffix}.",
                "en": f"The latest task related to this resource is **{title}**{suffix}.",
            }.get(language, f"La última tarea relacionada con este recurso es **{title}**{suffix}.")
        owner = subject_label
        return {
            "pt": f"A tarefa mais recente de {owner} é **{title}**{suffix}.",
            "en": f"{owner}'s latest task is **{title}**{suffix}.",
        }.get(language, f"La última tarea de {owner} es **{title}**{suffix}.")
    if perspective == "agent":
        return {
            "pt": f"A tarefa atual em que estou a trabalhar é **{title}**{suffix}.",
            "en": f"The current task I am working on is **{title}**{suffix}.",
        }.get(language, f"La tarea actual en la que estoy trabajando es **{title}**{suffix}.")
    if perspective == "requester":
        return {
            "pt": f"A tarefa atual em que está a trabalhar é **{title}**{suffix}.",
            "en": f"The current task you are working on is **{title}**{suffix}.",
        }.get(language, f"La tarea actual en la que estás trabajando es **{title}**{suffix}.")
    owner = subject_label or {"pt": "Este recurso", "en": "This resource"}.get(language, "Este recurso")
    return {
        "pt": f"A tarefa atual em que {owner} está a trabalhar é **{title}**{suffix}.",
        "en": f"The current task {owner} is working on is **{title}**{suffix}.",
    }.get(language, f"La tarea actual en la que trabaja {owner} es **{title}**{suffix}.")
