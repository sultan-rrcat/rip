"""Inbound message cap (max_message_chars) — fail honest with 413.

A single oversized paste must never silently consume the context window:
RunManager.create_run raises ValueError past the cap and the routes map it
to 413. Boundary input (exactly the cap) still creates a run.
"""

from __future__ import annotations

import uuid

import pytest

try:
    from app.agents.base import StepStatus
    from app.api.deps import get_run_manager
    from app.api.runs import router as runs_router
    from app.core.config import settings
    from app.core.db import pg_connection
    from app.orchestration.orchestrator import OrchestrationResult
    from app.orchestration.results import StepResult
    from app.routes.auth import UserResponse, get_current_user
    from app.runs.manager import RunManager
    from app.store import runs as run_store
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001 - import probe
    _IMPORT_ERROR = e


def _db_up() -> bool:
    if _IMPORT_ERROR is not None:
        return False
    try:
        with pg_connection():
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(
    _IMPORT_ERROR is not None or not _db_up(),
    reason=f"message limits need postgres ({_IMPORT_ERROR or 'connect failed'})",
)


class FakeProvider:
    def generate(self, model=None, messages=None, max_tokens=None, **kw):
        return "folded summary (fake)"


class FakeOrchestrator:
    def run(
        self,
        request_text,
        notebook_id,
        on_event=None,
        context=None,
        cancel_event=None,
        **_kw,
    ):
        emit = on_event or (lambda d: None)
        emit(
            {
                "type": "plan",
                "plan_id": "p1",
                "goal": request_text,
                "steps": [{"step_id": "1", "executor": "reasoning", "depends_on": []}],
            }
        )
        emit({"type": "step_started", "step_id": "1", "executor_id": "reasoning"})
        emit(
            {
                "type": "step_completed",
                "step_id": "1",
                "status": "success",
                "output": "done",
            }
        )
        return OrchestrationResult(
            trace_id="t1",
            plan_id="p1",
            goal=request_text,
            step_results=[
                StepResult(
                    step_id="1",
                    agent_id="reasoning",
                    status=StepStatus.SUCCESS,
                    output="done",
                    data={},
                )
            ],
            summary="done",
            status="success",
            plan_incomplete=False,
            conflicts=[],
            needs_clarification=False,
        )


@pytest.fixture()
def test_user():
    username = f"limits-{uuid.uuid4().hex[:8]}"
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) "
            "RETURNING user_id",
            (username, "not-a-real-hash"),
        )
        user_id = str(cur.fetchone()[0])
    yield UserResponse(user_id=user_id, username=username)
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))


@pytest.fixture()
def notebook_id(test_user):
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notebooks (notebook_name, owner_id) VALUES (%s, %s) "
            "RETURNING notebook_id",
            ("limits-test", test_user.user_id),
        )
        nb = str(cur.fetchone()[0])
    yield nb
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM notebooks WHERE notebook_id = %s", (nb,))


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    return RunManager(provider=FakeProvider(), orchestrator=FakeOrchestrator())


@pytest.fixture()
def client(manager, test_user):
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[get_run_manager] = lambda: manager
    app.dependency_overrides[get_current_user] = lambda: test_user
    with TestClient(app) as c:
        yield c


@needs_db
class TestMessageCap:
    def test_oversize_message_413(self, client, notebook_id):
        big = "x" * (int(settings.max_message_chars) + 1)
        r = client.post("/v1/runs", json={"notebook_id": notebook_id, "message": big})
        assert r.status_code == 413
        assert "too large" in r.json()["detail"]
        # Rejected before any run row exists — nothing to leak or replay.
        assert run_store.list_runs(notebook_id) == []

    def test_boundary_message_accepted(self, client, notebook_id):
        edge = "y" * int(settings.max_message_chars)
        r = client.post("/v1/runs", json={"notebook_id": notebook_id, "message": edge})
        assert r.status_code == 202, r.text

    def test_manager_raises_before_db(self, manager, notebook_id):
        with pytest.raises(ValueError, match="too large"):
            manager.create_run(notebook_id, "z" * (int(settings.max_message_chars) + 1))
        assert run_store.list_runs(notebook_id) == []
