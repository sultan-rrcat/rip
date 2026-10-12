"""Studio routes — Convert + Quiz MVP over the existing run worker.

Real Postgres store + real FastAPI routes, fake orchestrator/provider (no
LLM). Verifies: synthetic messages route deterministically, jobs link to
runs, history joins status, ownership is 404-on-foreign, validation is 422.
"""

from __future__ import annotations

import time
import uuid

import pytest

try:
    from app.agents.base import StepStatus
    from app.api.deps import get_run_manager
    from app.api.runs import router as runs_router
    from app.core.db import pg_connection
    from app.orchestration.orchestrator import OrchestrationResult
    from app.orchestration.results import StepResult
    from app.routes.auth import UserResponse, get_current_user
    from app.routes.studio import _quiz_message
    from app.routes.studio import router as studio_router
    from app.runs.manager import RunManager
    from app.store import runs as run_store
    from app.store import studio_jobs as job_store
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
    reason=f"studio needs postgres ({_IMPORT_ERROR or 'connect failed'})",
)


class FakeProvider:
    def generate(self, model=None, messages=None, max_tokens=None, **kw):
        return "folded summary (fake)"


def _ok_step():
    return StepResult(
        step_id="1",
        agent_id="reasoning",
        status=StepStatus.SUCCESS,
        output="done",
        data={},
    )


class FakeOrchestrator:
    """Records request_text so tests can assert deterministic routing."""

    def __init__(self):
        self.seen: list[tuple[str, str]] = []

    def run(
        self,
        request_text,
        notebook_id,
        on_event=None,
        context=None,
        cancel_event=None,
        **_kw,
    ):
        self.seen.append((notebook_id, request_text))
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
            step_results=[_ok_step()],
            summary="done",
            status="success",
            plan_incomplete=False,
            conflicts=[],
            needs_clarification=False,
        )


@pytest.fixture()
def test_user():
    username = f"studio-{uuid.uuid4().hex[:8]}"
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
            ("studio-test", test_user.user_id),
        )
        nb = str(cur.fetchone()[0])
    yield nb
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM notebooks WHERE notebook_id = %s", (nb,))


@pytest.fixture()
def orch():
    return FakeOrchestrator()


@pytest.fixture()
def manager(orch, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    return RunManager(provider=FakeProvider(), orchestrator=orch)


def _build_app(manager, user):
    app = FastAPI()
    app.include_router(runs_router)
    app.include_router(studio_router)
    app.dependency_overrides[get_run_manager] = lambda: manager
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture()
def client(manager, test_user):
    with TestClient(_build_app(manager, test_user)) as c:
        yield c


def _apply_schema():
    """Ensure the studio_jobs table exists (idempotent — schema.sql itself
    is only applied once per session by conftest)."""
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.studio_jobs
            (
                job_id      uuid NOT NULL DEFAULT gen_random_uuid(),
                notebook_id uuid NOT NULL,
                kind        text NOT NULL,
                params      jsonb NOT NULL DEFAULT '{}',
                run_id      uuid NOT NULL,
                created_at  timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT studio_jobs_pkey PRIMARY KEY (job_id),
                CONSTRAINT studio_jobs_notebook_id_fkey FOREIGN KEY (notebook_id)
                    REFERENCES public.notebooks (notebook_id)
                    ON UPDATE NO ACTION
                    ON DELETE CASCADE,
                CONSTRAINT studio_jobs_run_id_fkey FOREIGN KEY (run_id)
                    REFERENCES public.runs (id)
                    ON UPDATE NO ACTION
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_studio_jobs_notebook
                ON public.studio_jobs (notebook_id, created_at DESC);
            """
        )


def _wait_done(run_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = run_store.get_run(run_id)
        if row is not None and row.status in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


@needs_db
class TestStudioQuiz:
    def test_quiz_message_is_rule_routed(self):
        from app.orchestration.intents import Intent
        from app.orchestration.router import _rule_pre_filter

        assert _rule_pre_filter(_quiz_message(10, "mcq")) is Intent.QUIZ
        assert _rule_pre_filter(_quiz_message(8, "msq")) is Intent.QUIZ
        assert _rule_pre_filter(_quiz_message(5, "truefalse")) is Intent.QUIZ

    def test_create_quiz_job_202(self, client, notebook_id, orch):
        _apply_schema()
        r = client.post(
            f"/api/notebooks/{notebook_id}/studio/quiz",
            json={"count": 5, "kind": "mcq"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["kind"] == "quiz"
        assert body["params"] == {"count": 5, "kind": "mcq"}
        assert body["run_id"] and body["job_id"]
        # Synthetic message reached the worker verbatim.
        _wait_done(body["run_id"])
        assert orch.seen and orch.seen[-1][1] == _quiz_message(5, "mcq")
        # History joins live status.
        jobs = client.get(f"/api/notebooks/{notebook_id}/studio/jobs").json()
        assert len(jobs) == 1 and jobs[0]["job_id"] == body["job_id"]
        assert jobs[0]["status"] == "completed"

    def test_quiz_validation_422(self, client, notebook_id):
        _apply_schema()
        assert (
            client.post(
                f"/api/notebooks/{notebook_id}/studio/quiz", json={"count": 1}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/notebooks/{notebook_id}/studio/quiz", json={"count": 51}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/notebooks/{notebook_id}/studio/quiz", json={"kind": "essay"}
            ).status_code
            == 422
        )

    def test_quiz_unknown_notebook_404(self, client):
        _apply_schema()
        r = client.post(
            f"/api/notebooks/{uuid.uuid4()}/studio/quiz",
            json={"count": 5, "kind": "mcq"},
        )
        assert r.status_code == 404


@needs_db
class TestStudioConvert:
    def test_convert_all_message_is_rule_routed(self):
        from app.orchestration.intents import Intent
        from app.orchestration.router import _rule_pre_filter

        assert _rule_pre_filter("Convert all documents to pdf") is not None
        assert _rule_pre_filter("Convert all documents to pdf") == Intent.CONVERT_ALL

    def test_convert_all_job_202(self, client, notebook_id, orch):
        _apply_schema()
        r = client.post(
            f"/api/notebooks/{notebook_id}/studio/convert",
            json={"file_id": "*", "target_format": "pdf"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["kind"] == "convert"
        assert body["params"] == {"file_id": "*", "target_format": "pdf"}
        _wait_done(body["run_id"])
        assert orch.seen and "Convert all documents to pdf" in orch.seen[-1][1]

    def test_convert_one_job_uses_quoted_filename(
        self, client, notebook_id, orch, test_user
    ):
        _apply_schema()
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO files (notebook_id, file_name, file_size, file_status)"
                " VALUES (%s, %s, %s, 'ready') RETURNING file_id",
                (notebook_id, "report.pdf", 10),
            )
            file_id = str(cur.fetchone()[0])
        r = client.post(
            f"/api/notebooks/{notebook_id}/studio/convert",
            json={"file_id": file_id, "target_format": "docx"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["params"]["file_name"] == "report.pdf"
        _wait_done(body["run_id"])
        assert orch.seen and '"report.pdf"' in orch.seen[-1][1]

    def test_convert_validation(self, client, notebook_id):
        _apply_schema()
        assert (
            client.post(
                f"/api/notebooks/{notebook_id}/studio/convert",
                json={"file_id": "*", "target_format": "exe"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/notebooks/{notebook_id}/studio/convert",
                json={"file_id": str(uuid.uuid4()), "target_format": "pdf"},
            ).status_code
            == 404
        )

    def test_job_detail_and_ownership(self, client, notebook_id, test_user):
        _apply_schema()
        body = client.post(
            f"/api/notebooks/{notebook_id}/studio/quiz",
            json={"count": 3, "kind": "mcq"},
        ).json()
        _wait_done(body["run_id"])
        r = client.get(f"/api/notebooks/{notebook_id}/studio/jobs/{body['job_id']}")
        assert r.status_code == 200
        assert r.json()["run_id"] == body["run_id"]
        # Foreign notebook → 404, never 403.
        other = str(uuid.uuid4())
        assert (
            client.get(
                f"/api/notebooks/{other}/studio/jobs/{body['job_id']}"
            ).status_code
            == 404
        )


@needs_db
class TestStudioStore:
    def test_job_store_roundtrip(self, notebook_id):
        _apply_schema()
        run = run_store.create_run(notebook_id)
        job = job_store.create_job(notebook_id, "quiz", {"count": 4}, run.id)
        fetched = job_store.get_job(job.job_id)
        assert fetched is not None and fetched.run_id == run.id
        listed = job_store.list_jobs(notebook_id)
        assert any(j.job_id == job.job_id for j in listed)
