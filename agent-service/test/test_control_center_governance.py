import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.controllers import governance
from app.services.control_center_governance import ROLES, _password_hash, public_user


class ControlCenterGovernanceTests(unittest.TestCase):
    def test_password_derivation_uses_salt(self):
        self.assertNotEqual(_password_hash("a-secure-password", b"salt-one"),
                            _password_hash("a-secure-password", b"salt-two"))

    def test_public_user_never_exposes_password_material(self):
        user = public_user({
            "ID": "00000000-0000-0000-0000-000000000001", "Username": "audit",
            "DisplayName": "Auditor", "Role": "auditor", "PasswordHash": "secret",
            "PasswordSalt": "secret-salt", "active": True,
        })
        self.assertEqual(user["permissions"], ["read"])
        self.assertNotIn("PasswordHash", user)
        self.assertNotIn("PasswordSalt", user)

    def test_role_permissions_follow_least_privilege(self):
        self.assertEqual(ROLES["auditor"], {"read"})
        self.assertNotIn("approve", ROLES["operator"])
        self.assertIn("manage_users", ROLES["administrator"])

    def test_users_endpoint_rejects_non_administrator(self):
        app = FastAPI(); app.include_router(governance.router)
        app.dependency_overrides[governance._user] = lambda: {
            "ID": "00000000-0000-0000-0000-000000000001", "permissions": ["read"]
        }
        response = TestClient(app).get("/api/v1/control-center/users")
        self.assertEqual(response.status_code, 403)

    def test_login_rejects_invalid_credentials(self):
        app = FastAPI(); app.include_router(governance.router)
        with patch.dict(os.environ, {"CONTROL_CENTER_AUTH_ENABLED": "true"}), \
             patch.object(governance, "authenticate", return_value=None):
            response = TestClient(app).post("/api/v1/control-center/auth/login", json={
                "username": "admin", "password": "incorrect-password",
            })
        self.assertEqual(response.status_code, 401)

    def test_restore_requires_restore_permission(self):
        app = FastAPI(); app.include_router(governance.router)
        app.dependency_overrides[governance._user] = lambda: {
            "ID": "00000000-0000-0000-0000-000000000001", "permissions": ["read", "configure"]
        }
        response = TestClient(app).post(
            "/api/v1/control-center/changes/00000000-0000-0000-0000-000000000002/restore",
            json={"approvalId": "00000000-0000-0000-0000-000000000003"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
