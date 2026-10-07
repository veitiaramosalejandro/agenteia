from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.connectors.db_client import _postgres_connection


ROLES: dict[str, set[str]] = {
    "administrator": {"read", "configure", "operate", "approve", "restore", "manage_users"},
    "operator": {"read", "configure", "operate", "request_approval"},
    "auditor": {"read"},
}
_schema_lock = threading.Lock()
_schema_ready = False


def auth_enabled() -> bool:
    return os.getenv("CONTROL_CENTER_AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def ensure_governance_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        path = Path(__file__).with_name("control_center_governance.sql")
        with _postgres_connection() as connection:
            connection.execute(path.read_text(encoding="utf-8"))
        _schema_ready = True
    bootstrap_administrator()


def _password_hash(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return base64.b64encode(digest).decode("ascii")


def bootstrap_administrator() -> None:
    username = os.getenv("CONTROL_CENTER_BOOTSTRAP_USERNAME", "").strip().lower()
    password = os.getenv("CONTROL_CENTER_BOOTSTRAP_PASSWORD", "")
    if not username or len(password) < 12:
        return
    with _postgres_connection() as connection:
        exists = connection.execute('SELECT 1 FROM public."SysAgentIAAdminUser" LIMIT 1').fetchone()
        if exists:
            return
        salt = secrets.token_bytes(24)
        connection.execute(
            '''INSERT INTO public."SysAgentIAAdminUser"
               ("Username","DisplayName","PasswordHash","PasswordSalt","Role")
               VALUES (%s,%s,%s,%s,'administrator')''',
            (username, os.getenv("CONTROL_CENTER_BOOTSTRAP_DISPLAY_NAME", "Administrator")[:180],
             _password_hash(password, salt), base64.b64encode(salt).decode("ascii")),
        )


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    ensure_governance_schema()
    with _postgres_connection() as connection:
        row = connection.execute(
            '''SELECT * FROM public."SysAgentIAAdminUser"
               WHERE lower("Username")=lower(%s) AND active=true''', (username.strip(),),
        ).fetchone()
        if not row:
            hashlib.pbkdf2_hmac("sha256", password.encode(), b"invalid-user-salt", 310_000)
            return None
        expected = _password_hash(password, base64.b64decode(row["PasswordSalt"]))
        if not hmac.compare_digest(expected, row["PasswordHash"]):
            return None
        token = secrets.token_urlsafe(48)
        expires = datetime.now(timezone.utc) + timedelta(hours=max(1, min(24, int(os.getenv("CONTROL_CENTER_SESSION_HOURS", "8")))))
        connection.execute(
            '''INSERT INTO public."SysAgentIAAdminSession" ("IDUser","TokenHash","ExpiresAt")
               VALUES (%s,%s,%s)''', (row["ID"], hashlib.sha256(token.encode()).hexdigest(), expires),
        )
        connection.execute('UPDATE public."SysAgentIAAdminUser" SET "LastLoginAt"=CURRENT_TIMESTAMP WHERE "ID"=%s', (row["ID"],))
    return {"token": token, "expiresAt": expires, "user": public_user(row)}


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    role = str(row["Role"])
    return {"ID": row["ID"], "username": row["Username"], "displayName": row["DisplayName"],
            "role": role, "active": row.get("active", True), "permissions": sorted(ROLES.get(role, set())),
            "createdAt": row.get("CreatedAt"), "lastLoginAt": row.get("LastLoginAt")}


def resolve_session(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    ensure_governance_schema()
    with _postgres_connection() as connection:
        row = connection.execute(
            '''SELECT u.* FROM public."SysAgentIAAdminSession" s
               JOIN public."SysAgentIAAdminUser" u ON u."ID"=s."IDUser"
               WHERE s."TokenHash"=%s AND s."RevokedAt" IS NULL
                 AND s."ExpiresAt">CURRENT_TIMESTAMP AND u.active=true''',
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    return public_user(row) if row else None


def revoke_session(token: str) -> None:
    with _postgres_connection() as connection:
        connection.execute('UPDATE public."SysAgentIAAdminSession" SET "RevokedAt"=CURRENT_TIMESTAMP WHERE "TokenHash"=%s',
                           (hashlib.sha256(token.encode()).hexdigest(),))


def list_users() -> list[dict[str, Any]]:
    ensure_governance_schema()
    with _postgres_connection() as connection:
        rows = connection.execute('SELECT * FROM public."SysAgentIAAdminUser" ORDER BY "Username"').fetchall()
    return [public_user(row) for row in rows]


def has_users() -> bool:
    ensure_governance_schema()
    with _postgres_connection() as connection:
        return bool(connection.execute('SELECT 1 FROM public."SysAgentIAAdminUser" WHERE active=true LIMIT 1').fetchone())


def save_user(payload: dict[str, Any]) -> dict[str, Any]:
    salt = secrets.token_bytes(24)
    with _postgres_connection() as connection:
        row = connection.execute(
            '''INSERT INTO public."SysAgentIAAdminUser"
               ("Username","DisplayName","PasswordHash","PasswordSalt","Role") VALUES (%s,%s,%s,%s,%s)
               RETURNING *''',
            (payload["username"].strip().lower(), payload["displayName"].strip(),
             _password_hash(payload["password"], salt), base64.b64encode(salt).decode(), payload["role"]),
        ).fetchone()
    return public_user(row)


def set_user_active(user_id: UUID, active: bool) -> dict[str, Any] | None:
    with _postgres_connection() as connection:
        row = connection.execute(
            '''UPDATE public."SysAgentIAAdminUser" SET active=%s,"UpdatedAt"=CURRENT_TIMESTAMP
               WHERE "ID"=%s RETURNING *''', (active, user_id),
        ).fetchone()
        if not active:
            connection.execute('UPDATE public."SysAgentIAAdminSession" SET "RevokedAt"=CURRENT_TIMESTAMP WHERE "IDUser"=%s AND "RevokedAt" IS NULL', (user_id,))
    return public_user(row) if row else None


def create_approval(instance_id: UUID | None, user_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    with _postgres_connection() as connection:
        row = connection.execute(
            '''INSERT INTO public."SysAgentIAApproval"
               ("IDSolidSETInstance","Operation","ResourceType","ResourceID","Reason","RequestedBy")
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''',
            (instance_id, payload["operation"], payload["resourceType"], payload["resourceId"], payload["reason"], user_id),
        ).fetchone()
    return dict(row)


def list_approvals(instance_id: UUID | None = None, status: str | None = None) -> list[dict[str, Any]]:
    clauses, args = [], []
    if instance_id: clauses.append('a."IDSolidSETInstance"=%s'); args.append(instance_id)
    if status: clauses.append('a."Status"=%s'); args.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _postgres_connection() as connection:
        rows = connection.execute(
            '''SELECT a.*, ru."DisplayName" AS "RequestedByName", du."DisplayName" AS "DecidedByName"
               FROM public."SysAgentIAApproval" a
               JOIN public."SysAgentIAAdminUser" ru ON ru."ID"=a."RequestedBy"
               LEFT JOIN public."SysAgentIAAdminUser" du ON du."ID"=a."DecidedBy"''' + where +
            ' ORDER BY a."CreatedAt" DESC LIMIT 300', args).fetchall()
    return [dict(row) for row in rows]


def decide_approval(approval_id: UUID, user_id: UUID, decision: str, note: str) -> dict[str, Any] | None:
    with _postgres_connection() as connection:
        row = connection.execute(
            '''UPDATE public."SysAgentIAApproval" SET "Status"=%s,"DecidedBy"=%s,
               "DecisionNote"=NULLIF(%s,''),"DecidedAt"=CURRENT_TIMESTAMP
               WHERE "ID"=%s AND "Status"='pending' RETURNING *''',
            (decision, user_id, note[:500], approval_id),).fetchone()
    return dict(row) if row else None


def record_change(*, user_id: UUID | str | None, action: str, resource_type: str, resource_id: str | None,
                  method: str, path: str, status_code: int, instance_id: UUID | str | None = None,
                  before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
                  restored_from: UUID | None = None) -> None:
    ensure_governance_schema()
    with _postgres_connection() as connection:
        connection.execute(
            '''INSERT INTO public."SysAgentIAChangeHistory"
               ("IDSolidSETInstance","IDUser","Action","ResourceType","ResourceID","Method","Path",
                "StatusCode","BeforeState","AfterState","RestoredFrom")
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)''',
            (instance_id, user_id, action[:80], resource_type[:80], resource_id, method, path[:500], status_code,
             json.dumps(before, default=str) if before else None, json.dumps(after, default=str) if after else None,
             restored_from),)


def list_changes(instance_id: UUID | None = None, limit: int = 200) -> list[dict[str, Any]]:
    where, args = ('WHERE h."IDSolidSETInstance"=%s', [instance_id]) if instance_id else ('', [])
    args.append(max(1, min(limit, 500)))
    with _postgres_connection() as connection:
        rows = connection.execute(
            '''SELECT h.*,u."DisplayName" AS "UserName" FROM public."SysAgentIAChangeHistory" h
               LEFT JOIN public."SysAgentIAAdminUser" u ON u."ID"=h."IDUser" ''' + where +
            ' ORDER BY h."CreatedAt" DESC LIMIT %s', args).fetchall()
    return [dict(row) for row in rows]


def consume_restore_approval(approval_id: UUID, change_id: UUID, user_id: UUID) -> dict[str, Any] | None:
    with _postgres_connection() as connection:
        approval = connection.execute(
            '''SELECT * FROM public."SysAgentIAApproval" WHERE "ID"=%s AND "Status"='approved'
               AND "Operation"='restore' AND "ResourceID"=%s FOR UPDATE''', (approval_id, str(change_id)),
        ).fetchone()
        if not approval:
            return None
        change = connection.execute('SELECT * FROM public."SysAgentIAChangeHistory" WHERE "ID"=%s', (change_id,)).fetchone()
        if not change or not change.get("BeforeState") or change["ResourceType"] != "automation_rule":
            return None
        # Restoration is deliberately limited to an explicit allowlist.
        state = dict(change["BeforeState"])
        kind = change["ResourceType"]
        allowed = {"Name", "TriggerType", "Instruction", "RequiredCapabilities", "MaxRunsPerHour", "RequireApproval", "active"}
        values = {key: state[key] for key in allowed if key in state}
        assignments = ",".join(f'"{key}"=%s' if key != "active" else 'active=%s' for key in values)
        if not assignments:
            return None
        restored = connection.execute(
            f'UPDATE public."SysAgentIAAutomationRule" SET {assignments},"UpdatedAt"=CURRENT_TIMESTAMP WHERE "ID"=%s RETURNING "ID"',
            [*values.values(), change["ResourceID"]],
        ).fetchone()
        if not restored:
            return None
        connection.execute('UPDATE public."SysAgentIAApproval" SET "Status"=\'consumed\',"ConsumedAt"=CURRENT_TIMESTAMP WHERE "ID"=%s', (approval_id,))
        connection.execute(
            '''INSERT INTO public."SysAgentIAChangeHistory"
               ("IDSolidSETInstance","IDUser","Action","ResourceType","ResourceID","Method","Path",
                "StatusCode","AfterState","RestoredFrom")
               VALUES (%s,%s,'restore',%s,%s,'POST','control-center/changes/restore',200,%s::jsonb,%s)''',
            (change.get("IDSolidSETInstance"), user_id, kind, change["ResourceID"], json.dumps(state, default=str), change_id),
        )
    return {"status": "restored", "resourceType": kind, "resourceId": change["ResourceID"]}

