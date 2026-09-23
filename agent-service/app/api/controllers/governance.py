from __future__ import annotations

from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.connectors.db_client import get_solidset_instance
from app.services.control_center_governance import (
    authenticate, auth_enabled, consume_restore_approval, create_approval,
    decide_approval, ensure_governance_schema, has_users, list_approvals, list_changes,
    list_users, revoke_session, save_user, set_user_active,
)


router = APIRouter(prefix="/api/v1/control-center", tags=["Control Center Governance"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024)


class UserRequest(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,120}$")
    displayName: str = Field(min_length=2, max_length=180)
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["administrator", "operator", "auditor"]


class UserStateRequest(BaseModel):
    active: bool


class ApprovalRequest(BaseModel):
    instanceCode: str | None = Field(default=None, max_length=120)
    operation: Literal["restore", "publish", "deliver", "destructive_change"]
    resourceType: str = Field(min_length=1, max_length=80)
    resourceId: str = Field(min_length=1, max_length=180)
    reason: str = Field(min_length=8, max_length=500)


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=500)


class RestoreRequest(BaseModel):
    approvalId: UUID


def _user(request: Request) -> dict:
    user = getattr(request.state, "control_center_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Autenticación administrativa requerida.")
    return user


def _permission(name: str):
    def dependency(user: dict = Depends(_user)) -> dict:
        if name not in user.get("permissions", []):
            raise HTTPException(status_code=403, detail=f"Permiso requerido: {name}.")
        return user
    return dependency


@router.get("/auth/status")
def authentication_status() -> dict:
    environment = __import__("os").environ
    bootstrap_ready = bool(environment.get("CONTROL_CENTER_BOOTSTRAP_USERNAME", "").strip()) \
        and len(environment.get("CONTROL_CENTER_BOOTSTRAP_PASSWORD", "")) >= 12
    try:
        configured = has_users() or bootstrap_ready
    except psycopg.Error:
        configured = bootstrap_ready
    return {
        "enabled": auth_enabled(),
        "configured": configured,
    }


@router.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    if not auth_enabled():
        raise HTTPException(status_code=409, detail="La autenticación administrativa no está habilitada.")
    try:
        result = authenticate(payload.username, payload.password)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo consultar el almacén de autenticación.") from exc
    if not result:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    return result


@router.get("/auth/me")
def me(user: dict = Depends(_user)) -> dict:
    return user


@router.post("/auth/logout", status_code=204)
def logout(request: Request, user: dict = Depends(_user)) -> None:
    del user
    authorization = request.headers.get("authorization", "")
    revoke_session(authorization[7:].strip() if authorization.lower().startswith("bearer ") else "")


@router.get("/users")
def users(user: dict = Depends(_permission("manage_users"))) -> dict:
    del user
    return {"items": list_users()}


@router.post("/users", status_code=201)
def create_user(payload: UserRequest, user: dict = Depends(_permission("manage_users"))) -> dict:
    del user
    try:
        return save_user(payload.model_dump())
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="El usuario ya existe.") from exc


@router.patch("/users/{user_id}")
def update_user_state(user_id: UUID, payload: UserStateRequest,
                      actor: dict = Depends(_permission("manage_users"))) -> dict:
    if str(user_id) == str(actor["ID"]) and not payload.active:
        raise HTTPException(status_code=409, detail="No puedes desactivar tu propia cuenta.")
    result = set_user_active(user_id, payload.active)
    if not result:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return result


def _instance_id(code: str | None):
    if not code:
        return None
    instance = get_solidset_instance(code=code, source_ip=None)
    if not instance:
        raise HTTPException(status_code=404, detail="Instancia SolidSET no encontrada.")
    return instance["ID"]


@router.get("/approvals")
def approvals(instanceCode: str | None = None, approvalStatus: str | None = Query(default=None, alias="status"),
              user: dict = Depends(_permission("read"))) -> dict:
    del user
    return {"items": list_approvals(_instance_id(instanceCode), approvalStatus)}


@router.post("/approvals", status_code=201)
def request_approval(payload: ApprovalRequest, user: dict = Depends(_user)) -> dict:
    if not ({"request_approval", "approve"} & set(user.get("permissions", []))):
        raise HTTPException(status_code=403, detail="No tienes permiso para solicitar aprobaciones.")
    return create_approval(_instance_id(payload.instanceCode), user["ID"], payload.model_dump())


@router.post("/approvals/{approval_id}/decision")
def approval_decision(approval_id: UUID, payload: ApprovalDecision,
                      user: dict = Depends(_permission("approve"))) -> dict:
    result = decide_approval(approval_id, user["ID"], payload.decision, payload.note)
    if not result:
        raise HTTPException(status_code=409, detail="La aprobación ya fue decidida o no existe.")
    return result


@router.get("/changes")
def changes(instanceCode: str | None = None, limit: int = Query(default=200, ge=1, le=500),
            user: dict = Depends(_permission("read"))) -> dict:
    del user
    return {"items": list_changes(_instance_id(instanceCode), limit)}


@router.post("/changes/{change_id}/restore")
def restore(change_id: UUID, payload: RestoreRequest,
            user: dict = Depends(_permission("restore"))) -> dict:
    result = consume_restore_approval(payload.approvalId, change_id, user["ID"])
    if not result:
        raise HTTPException(
            status_code=409,
            detail="La restauración no está permitida: falta una aprobación válida o el cambio no contiene un estado restaurable.",
        )
    return result
