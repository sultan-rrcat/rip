"""Role-based access tests (ADR-052): admin vs viewer.

- No-DB tests (always run): `normalize_role` validation + `require_admin`
  gating via FastAPI `dependency_overrides` on a throwaway app.
- DB tests (same skip contract as the rest of the suite): role column
  default, `/api/auth/me` + login expose the role, viewers get 403 on
  every `/v1/admin/*` verb while admins pass.
"""

import os
import sys

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.routes.auth import (
    DEFAULT_ROLE,
    VALID_ROLES,
    UserResponse,
    get_current_user,
    normalize_role,
    require_admin,
)


def _guarded_app(user: UserResponse) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: user

    @app.get("/guarded")
    def guarded(u: UserResponse = Depends(require_admin)):  # noqa: B008 - FastAPI Depends-in-default is canonical
        return {"ok": True, "role": u.role}

    return app


class TestNormalizeRole:
    def test_accepts_both_roles_case_insensitive(self):
        assert normalize_role("admin") == "admin"
        assert normalize_role("viewer") == "viewer"
        assert normalize_role(" Admin ") == "admin"

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            normalize_role("superuser")
        with pytest.raises(ValueError):
            normalize_role("")
        with pytest.raises(ValueError):
            normalize_role(None)

    def test_defaults(self):
        assert DEFAULT_ROLE == "viewer"
        assert set(VALID_ROLES) == {"admin", "viewer"}


class TestRequireAdminNoDb:
    def test_admin_passes(self):
        app = _guarded_app(UserResponse(user_id="u1", username="a", role="admin"))
        with TestClient(app) as client:
            res = client.get("/guarded")
        assert res.status_code == 200
        assert res.json() == {"ok": True, "role": "admin"}

    def test_viewer_gets_403(self):
        app = _guarded_app(UserResponse(user_id="u2", username="v", role="viewer"))
        with TestClient(app) as client:
            res = client.get("/guarded")
        assert res.status_code == 403
        assert res.json()["detail"] == "Admin access required"

    def test_legacy_user_without_role_defaults_viewer(self):
        # Pydantic default covers rows/auth payloads that predate roles.
        user = UserResponse(user_id="u3", username="legacy")
        assert user.role == "viewer"
        app = _guarded_app(user)
        with TestClient(app) as client:
            res = client.get("/guarded")
        assert res.status_code == 403


class TestRolesDb:
    """Real-DB role checks (skipped with the suite when Postgres is down)."""

    @staticmethod
    def _temp_user(client, username, password, role="viewer"):
        """Register + login, then delete the user (CASCADE drops sessions)."""
        import uuid as _uuid
        from contextlib import contextmanager as _cm

        name = f"{username}-{_uuid.uuid4().hex[:6]}"

        @_cm
        def _ctx():
            from app.core.db import pg_connection
            from app.routes.auth import create_user

            create_user(name, password, role=role)
            try:
                res = client.post(
                    "/api/auth/login",
                    json={"username": name, "password": password},
                )
                assert res.status_code == 200, res.text
                yield res.json()
            finally:
                with pg_connection() as conn, conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username = %s", (name,))

        return _ctx()

    def test_create_user_defaults_viewer(self, client):
        from app.core.db import pg_connection
        from app.routes.auth import create_user

        name = "pytest-role-default"
        create_user(name, "pw")
        try:
            with pg_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT role FROM users WHERE username = %s", (name,))
                row = cur.fetchone()
            assert row is not None and row[0] == "viewer"
        finally:
            with pg_connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE username = %s", (name,))

    def test_login_and_me_expose_role(self, client):
        with self._temp_user(client, "pytest-role-admin", "pw", role="admin") as body:
            assert body["role"] == "admin"
            me = client.get("/api/auth/me")
            assert me.status_code == 200
            assert me.json()["role"] == "admin"

    def test_viewer_forbidden_on_admin_verbs(self, client):
        with self._temp_user(client, "pytest-role-viewer", "pw", role="viewer"):
            assert client.get("/v1/admin/health").status_code == 403
            assert client.get("/v1/admin/config").status_code == 403
            assert client.get("/v1/admin/prompts").status_code == 403
            assert client.put("/v1/admin/config", json={"updates": {}}).status_code == 403

    def test_admin_allowed_on_admin_reads(self, client):
        with self._temp_user(client, "pytest-role-admin2", "pw2", role="admin"):
            assert client.get("/v1/admin/health").status_code == 200
            assert client.get("/v1/admin/config").status_code == 200
            assert client.get("/v1/admin/prompts").status_code == 200
