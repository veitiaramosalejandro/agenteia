from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request

from app.agent.capabilities import normalize_capabilities
from app.api.controllers.agent_management import handle_multi_agent_dialogue
from app.api.schemas.automation import (
    AutomationEvaluationRequest,
    AutomationExecutionRequest,
    AutomationRuleRequest,
    WorkRoomAgentConfiguration,
)
from app.api.schemas.common import MultiAgentDialogueRequest
from app.connectors.db_client import (
    automation_runs_last_hour,
    configure_instance_agent_workroom,
    deactivate_automation_rule,
    get_active_agent_identity_for_resource,
    get_agent_model_configurations,
    get_automation_rule,
    get_solidset_instance,
    list_automation_rules,
    list_instance_workrooms,
    record_automation_run,
    save_automation_rule,
)
from app.services.control_center_governance import record_change


router = APIRouter(tags=["Agent Automation"])


def _instance(code: str) -> dict[str, Any]:
    value = get_solidset_instance(code=str(code or "").strip(), source_ip=None)
    if not value:
        raise HTTPException(status_code=404, detail="A instância SolidSET não existe ou está inativa.")
    return value


def _workroom(instance_id: UUID | str, workroom_id: UUID | str) -> dict[str, Any]:
    for item in list_instance_workrooms(instance_id):
        if str(item["IDWorkRoom"]) == str(workroom_id):
            return item
    raise HTTPException(status_code=404, detail="O canal não pertence à instância selecionada.")


@router.get("/api/v1/agent/solidset/instances/{code}/workrooms")
def read_instance_workrooms(code: str) -> dict[str, Any]:
    instance = _instance(code)
    try:
        items = list_instance_workrooms(instance["ID"])
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível consultar os canais.") from exc
    return {"instanceCode": instance["Code"], "total": len(items), "items": items}


@router.put(
    "/api/v1/agent/solidset/instances/{code}/workrooms/{workroom_id}/agents/{resource_id}"
)
def set_instance_workroom_agent(
    code: str,
    workroom_id: UUID,
    resource_id: UUID,
    request: WorkRoomAgentConfiguration,
) -> dict[str, Any]:
    instance = _instance(code)
    _workroom(instance["ID"], workroom_id)
    if not get_active_agent_identity_for_resource(resource_id, instance["ID"]):
        raise HTTPException(status_code=404, detail="O agente não pertence à instância selecionada.")
    try:
        saved = configure_instance_agent_workroom(
            instance["ID"], resource_id, workroom_id,
            active=request.active, response_order=request.response_order,
        )
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(status_code=409, detail="Sincronize o agente e o canal antes de configurar a relação.") from exc
    return {"status": "saved", "configuration": saved}


@router.get("/api/v1/agent/solidset/instances/{code}/automation-rules")
def read_automation_rules(code: str) -> dict[str, Any]:
    instance = _instance(code)
    try:
        items = list_automation_rules(instance["ID"])
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível consultar as regras.") from exc
    return {"instanceCode": instance["Code"], "total": len(items), "items": items}


@router.post("/api/v1/agent/solidset/instances/{code}/automation-rules", status_code=201)
def create_automation_rule(code: str, request: AutomationRuleRequest, http_request: Request = None) -> dict[str, Any]:
    instance = _instance(code)
    room = _workroom(instance["ID"], request.IDWorkRoom)
    if not get_active_agent_identity_for_resource(request.IDResource, instance["ID"]):
        raise HTTPException(status_code=404, detail="O agente não pertence à instância selecionada.")
    assignment = next(
        (row for row in room.get("agents") or [] if str(row.get("IDResource")) == str(request.IDResource) and row.get("active")),
        None,
    )
    if not assignment:
        raise HTTPException(status_code=409, detail="Ative o agente neste canal antes de criar a regra.")
    try:
        saved = save_automation_rule(instance["ID"], request.model_dump())
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Não foi possível guardar a regra.") from exc
    actor = getattr(http_request.state, "control_center_user", None) if http_request else None
    if actor:
        record_change(
            user_id=actor["ID"], action="create", resource_type="automation_rule",
            resource_id=str(saved["ID"]), method="POST", path=str(http_request.url.path),
            status_code=201, instance_id=instance["ID"], after=saved,
        )
    return {"status": "created", "rule": saved}


@router.delete("/api/v1/agent/solidset/instances/{code}/automation-rules/{rule_id}")
def remove_automation_rule(code: str, rule_id: UUID, request: Request = None) -> dict[str, Any]:
    instance = _instance(code)
    previous = get_automation_rule(rule_id, instance["ID"])
    if not deactivate_automation_rule(rule_id, instance["ID"]):
        raise HTTPException(status_code=404, detail="Regra ativa não encontrada.")
    actor = getattr(request.state, "control_center_user", None) if request else None
    if actor and previous:
        record_change(
            user_id=actor["ID"], action="deactivate", resource_type="automation_rule",
            resource_id=str(rule_id), method="DELETE", path=str(request.url.path), status_code=200,
            instance_id=instance["ID"], before=previous, after={**previous, "active": False},
        )
    return {"status": "deactivated", "ID": rule_id}


def _evaluate(instance: dict[str, Any], rule: dict[str, Any], approved: bool) -> dict[str, Any]:
    reasons: list[str] = []
    if not rule.get("active"):
        reasons.append("rule_inactive")
    room = _workroom(instance["ID"], rule["IDWorkRoom"])
    assignment = next(
        (row for row in room.get("agents") or [] if str(row.get("IDResource")) == str(rule["IDResource"]) and row.get("active")),
        None,
    )
    if not assignment:
        reasons.append("agent_not_active_in_workroom")
    configured: set[str] = set()
    for model in get_agent_model_configurations(rule["IDResource"], instance["ID"]):
        if model.get("active", True):
            configured.update(normalize_capabilities(model.get("Capabilities") or []))
    required = normalize_capabilities(rule.get("RequiredCapabilities") or [])
    missing = sorted(required - configured)
    if missing:
        reasons.append("missing_capabilities:" + ",".join(missing))
    runs = automation_runs_last_hour(rule["ID"])
    if runs >= int(rule.get("MaxRunsPerHour") or 1):
        reasons.append("hourly_limit_reached")
    if rule.get("RequireApproval") and not approved:
        reasons.append("approval_required")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "configuredCapabilities": sorted(configured),
        "requiredCapabilities": sorted(required),
        "runsLastHour": runs,
        "maxRunsPerHour": int(rule.get("MaxRunsPerHour") or 1),
    }


@router.post("/api/v1/agent/solidset/instances/{code}/automation-rules/{rule_id}/evaluate")
def evaluate_automation_rule(
    code: str, rule_id: UUID, request: AutomationEvaluationRequest,
) -> dict[str, Any]:
    instance = _instance(code)
    rule = get_automation_rule(rule_id, instance["ID"])
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    return {"rule": rule, **_evaluate(instance, rule, request.Approved)}


@router.post("/api/v1/agent/solidset/instances/{code}/automation-rules/{rule_id}/execute")
async def execute_automation_rule(
    code: str, rule_id: UUID, request: AutomationExecutionRequest,
) -> dict[str, Any]:
    instance = _instance(code)
    rule = get_automation_rule(rule_id, instance["ID"])
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    evaluation = _evaluate(instance, rule, request.Approved)
    if request.SendToSolidSET and not request.Approved:
        evaluation["reasons"] = list(dict.fromkeys([
            *evaluation["reasons"], "approval_required_for_delivery"
        ]))
        evaluation["eligible"] = False
    if not evaluation["eligible"]:
        run = record_automation_run(
            rule_id, status="blocked", input_text=request.Message,
            approved=request.Approved, error=";".join(evaluation["reasons"]),
        )
        return {"status": "blocked", "evaluation": evaluation, "run": run}
    effective_message = request.Message.strip()
    instruction = str(rule.get("Instruction") or "").strip()
    if instruction:
        effective_message = f"Instrução configurada para esta tarefa: {instruction}\n\nPedido: {effective_message}"
    try:
        dialogue = await handle_multi_agent_dialogue(
            MultiAgentDialogueRequest(
                IDWorkRoom=rule["IDWorkRoom"], RawMessage=effective_message,
                SelectedAgentResourceIds=[rule["IDResource"]],
                SenderResourceId=request.SenderResourceId,
                SendToSolidSET=request.SendToSolidSET,
                SolidSETInstanceCode=str(instance["Code"]),
            )
        )
        output = "\n\n".join(answer.response for answer in dialogue.responses)
        sent = any(answer.sent for answer in dialogue.responses)
        run = record_automation_run(
            rule_id, status="completed", input_text=request.Message,
            output_text=output, approved=request.Approved, sent=sent,
        )
        return {"status": "completed", "evaluation": evaluation, "run": run, "dialogue": dialogue}
    except Exception as exc:
        record_automation_run(
            rule_id, status="failed", input_text=request.Message,
            approved=request.Approved, error=f"{type(exc).__name__}: {str(exc)[:1000]}",
        )
        raise
