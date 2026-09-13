"""Phase 4.1 — runs API + Q31 worker + Q32/Q34/Q35 events.

Needs a live Postgres with schema.sql applied (compose `postgres`
service; from the host: DB_HOST=127.0.0.1 DB_PORT=5433 — note 127.0.0.1,
`localhost` costs ~21s/connect on Windows). Skips when unreachable. Run
with ``pytest backend/tests/test_runs_api.py -q --noconftest`` until the
torch env is repaired.

Strategy: REAL Postgres store + REAL FastAPI routes, FAKE orchestrator and
provider (no LLM calls). The worker thread runs for real, so event
persistence, SSE replay, sources, artifacts, memory and cancel are all
exercised end to end.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

try:
    from app.agents.base import StepStatus
    from app.agents.registry import AgentRegistry
    from app.api import deps as api_deps
    from app.api.admin import router as admin_router
    from app.api.deps import get_run_manager
    from app.api.runs import router as runs_router
    from app.core.db import pg_connection
    from app.orchestration.orchestrator import (
        OrchestrationError,
        OrchestrationResult,
    )
    from app.orchestration.results import StepResult
    from app.runs.manager import RunManager
    from app.store import runs as store
    from app.tools.registry import ToolRegistry
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001 - import probe; pragma: no cover
    _IMPORT_ERROR = e


def _db_up() -> bool:
    if _IMPORT_ERROR is not None:
        return False
    try:
        with pg_connection():
            pass
        return True
    except Exception:  # noqa: BLE001 - any connect failure means "down"
        return False


needs_db = pytest.mark.skipif(
    _IMPORT_ERROR is not None or not _db_up(),
    reason=f"runs api needs postgres ({_IMPORT_ERROR or 'connect failed'})",
)


class FakeProvider:
    def generate(self, model=None, messages=None, max_tokens=None, **kw):
        return "folded summary (fake)"


def _rag_step():
    return StepResult(
        step_id="s1",
        agent_id="rag.query",
        status=StepStatus.SUCCESS,
        output="chunks...",
        data={
            "results": [
                {"source": "doc.pdf", "section": "Intro", "content": "hello"},
                {"source": "doc.pdf", "section": "Intro", "content": "hello"},
                {"source": "other.pdf", "section": "Body", "content": "world"},
            ],
            "query": "hello",
        },
    )


def _chart_step():
    return StepResult(
        step_id="s2",
        agent_id="plot.chart",
        status=StepStatus.SUCCESS,
        output="<svg>...</svg>",
        data={"svg": "<svg xmlns='http://www.w3.org/2000/svg'></svg>"},
    )


def _result():
    return OrchestrationResult(
        trace_id="t1",
        plan_id="p1",
        goal="answer hello",
        step_results=[_rag_step(), _chart_step()],
        summary="final answer",
        status="success",
        plan_incomplete=False,
        conflicts=[],
        needs_clarification=False,
    )


class FakeOrchestrator:
    """Emits a realistic event chain, then returns a fixed result."""

    def run(self, request_text, notebook_id, on_event=None, context=None,
            cancel_event=None):
        emit = on_event or (lambda d: None)
        emit({"type": "plan", "plan_id": "p1", "goal": "answer hello",
              "steps": [{"step_id": "s1", "executor": "rag.query",
                         "depends_on": []}]})
        emit({"type": "step_started", "step_id": "s1", "executor_id": "rag.query"})
        emit({"type": "delta", "step_id": "s1", "content": "hel"})
        emit({"type": "delta", "step_id": "s1", "content": "lo"})
        emit({"type": "step_completed", "step_id": "s1", "status": "success",
              "output": "chunks..."})
        return _result()


class BlockingOrchestrator:
    """Blocks until released or cancelled (cancel-path test)."""

    def __init__(self):
        self.release = threading.Event()

    def run(self, request_text, notebook_id, on_event=None, context=None,
            cancel_event=None):
        emit = on_event or (lambda d: None)
        emit({"type": "plan", "plan_id": "p1", "goal": "g", "steps": []})
        while not self.release.is_set():
            if cancel_event is not None and cancel_event.is_set():
                raise OrchestrationError("run cancelled")
            time.sleep(0.02)
        return _result()


class BoomOrchestrator:
    def run(self, request_text, notebook_id, on_event=None, context=None,
            cancel_event=None):
        raise RuntimeError("boom")


@pytest.fixture()
def notebook_id():
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notebooks (notebook_name) VALUES (%s) "
            "RETURNING notebook_id",
            ("runs-api-test",),
        )
        nb = str(cur.fetchone()[0])
    yield nb
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM notebooks WHERE notebook_id = %s", (nb,))


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    """Point settings.upload_dir at a tmp dir (routes read settings live,
    mirroring production where worker and routes share the same root)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    return str(tmp_path)


@pytest.fixture()
def manager(uploads):
    return RunManager(
        provider=FakeProvider(),
        orchestrator=FakeOrchestrator(),
    )


@pytest.fixture()
def client(manager):
    app = FastAPI()
    app.include_router(runs_router)
    app.include_router(admin_router)
    app.dependency_overrides[get_run_manager] = lambda: manager
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean_deps():
    api_deps.reset()
    yield
    api_deps.reset()


def _wait_done(run_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = store.get_run(run_id)
        if row is not None and row.status in (
            "completed", "failed", "cancelled",
        ):
            return row
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


def _frames(text):
    """Parse SSE text into [(id, data-dict)]."""
    out = []
    for chunk in text.split("\n\n"):
        lines = [ln for ln in chunk.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        sid, data = None, None
        for ln in lines:
            if ln.startswith("id:"):
                sid = ln[3:].strip()
            elif ln.startswith("data:"):
                data = json.loads(ln[5:].strip())
        if data is not None:
            out.append((sid, data))
    return out


@needs_db
class TestCreateAndReplay:
    def test_create_run_202(self, client, notebook_id):
        r = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        )
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        assert run_id and set(r.json()) == {"run_id"}  # bare, no envelope
        row = _wait_done(run_id)
        assert row.status == "completed" and row.goal == "answer hello"

    def test_events_replay_no_deltas(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        r = client.get(f"/v1/runs/{run_id}/events")
        assert r.status_code == 200
        frames = _frames(r.text)
        types = [d["type"] for _, d in frames]
        for expected in (
            "run_started", "plan", "step_started", "step_completed",
            "sources", "summary", "artifacts", "run_completed",
        ):
            assert expected in types, f"missing {expected} in {types}"
        assert "delta" not in types  # Q35: live-only, never replayed
        seqs = [s for s, _ in frames]
        assert seqs == sorted(seqs, key=int)  # gap-free persisted order
        assert all(d["run_id"] == run_id for _, d in frames)

    def test_sources_shape(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        frames = _frames(client.get(f"/v1/runs/{run_id}/events").text)
        src = [d for _, d in frames if d["type"] == "sources"]
        assert len(src) == 1 and src[0]["step_id"] == "s1"
        # Q32 via extract_sources(): deduped (set-based, order not promised).
        assert sorted(map(json.dumps, src[0]["sources"],)) == sorted(
            map(json.dumps, [
                {"source": "doc.pdf", "section": "Intro"},
                {"source": "other.pdf", "section": "Body"},
            ])
        )

    def test_store_has_no_deltas(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        types = [e.event_type for e in store.list_events(run_id)]
        assert "delta" not in types

    def test_detail(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        r = client.get(f"/v1/runs/{run_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == run_id and body["status"] == "completed"
        assert body["notebook_id"] == notebook_id


@needs_db
class TestValidation:
    def test_empty_message_422(self, client, notebook_id):
        r = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "   "}
        )
        assert r.status_code == 422

    def test_unknown_notebook_404(self, client):
        r = client.post(
            "/v1/runs",
            json={"notebook_id": str(uuid.uuid4()), "message": "hi"},
        )
        assert r.status_code == 404

    def test_unknown_run_404(self, client):
        bad = str(uuid.uuid4())
        assert client.get(f"/v1/runs/{bad}").status_code == 404
        assert client.get(f"/v1/runs/{bad}/events").status_code == 404
        assert client.post(f"/v1/runs/{bad}/cancel").status_code == 404


@needs_db
class TestArtifacts:
    def test_artifact_download(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        frames = _frames(client.get(f"/v1/runs/{run_id}/events").text)
        arts = [d for _, d in frames if d["type"] == "artifacts"]
        assert len(arts) == 1
        entry = arts[0]["artifacts"][0]
        assert set(entry) >= {"artifact_id", "kind", "filename", "url"}
        assert "base64" not in json.dumps(arts[0])  # Q34: links, not bytes
        dl = client.get(entry["url"])
        assert dl.status_code == 200
        assert "<svg" in dl.text

    def test_unknown_artifact_404(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        r = client.get(f"/v1/runs/{run_id}/artifacts/nope")
        assert r.status_code == 404


@needs_db
class TestMemory:
    def test_long_history_folds_summary(self, client, notebook_id, manager):
        with pg_connection() as conn, conn.cursor() as cur:
            for i in range(12):
                role = "user" if i % 2 == 0 else "assistant"
                cur.execute(
                    "INSERT INTO messages (notebook_id, role, text) "
                    "VALUES (%s, %s, %s)",
                    (notebook_id, role, f"turn {i} " + "x" * 40),
                )
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT conversation_summary, summary_message_count "
                "FROM notebooks WHERE notebook_id = %s",
                (notebook_id,),
            )
            summary, count = cur.fetchone()
        assert summary == "folded summary (fake)"
        assert count == 2  # 12 turns, window 10 → oldest 2 folded

    def test_worker_never_writes_messages(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM messages WHERE notebook_id = %s",
                (notebook_id,),
            )
            assert cur.fetchone()[0] == 0  # Q22: frontend owns messages


@needs_db
class TestCancel:
    def test_cancel_running_run(self, notebook_id, uploads):
        orch = BlockingOrchestrator()
        manager = RunManager(
            provider=FakeProvider(), orchestrator=orch,
        )
        app = FastAPI()
        app.include_router(runs_router)
        app.dependency_overrides[get_run_manager] = lambda: manager
        with TestClient(app) as client:
            run_id = client.post(
                "/v1/runs", json={"notebook_id": notebook_id, "message": "hi"}
            ).json()["run_id"]
            time.sleep(0.5)  # let the worker block inside orchestration
            r = client.post(f"/v1/runs/{run_id}/cancel")
            assert r.status_code == 200
            body = r.json()
            assert body["run_id"] == run_id and body["already_done"] is False
            row = _wait_done(run_id)
            assert row.status == "cancelled"
            assert body["status"] == "cancelled"
            types = [e.event_type for e in store.list_events(run_id)]
            assert "cancelled" in types and "run_completed" not in types

    def test_cancel_finished_run_already_done(self, client, notebook_id):
        run_id = client.post(
            "/v1/runs", json={"notebook_id": notebook_id, "message": "hello"}
        ).json()["run_id"]
        _wait_done(run_id)
        r = client.post(f"/v1/runs/{run_id}/cancel")
        assert r.status_code == 200 and r.json()["already_done"] is True
        assert store.get_run(run_id).status == "completed"  # never overwritten


@needs_db
class TestFailure:
    def test_crash_becomes_error(self, notebook_id, uploads):
        manager = RunManager(
            provider=FakeProvider(), orchestrator=BoomOrchestrator(),
        )
        app = FastAPI()
        app.include_router(runs_router)
        app.dependency_overrides[get_run_manager] = lambda: manager
        with TestClient(app) as client:
            run_id = client.post(
                "/v1/runs", json={"notebook_id": notebook_id, "message": "hi"}
            ).json()["run_id"]
            row = _wait_done(run_id)
            assert row.status == "failed"
            types = [e.event_type for e in store.list_events(run_id)]
            assert "error" in types and "run_completed" not in types


@needs_db
class TestLiveDeltas:
    def test_delta_live_only_fractional_seq(self, notebook_id, uploads):
        orch = BlockingOrchestrator()
        manager = RunManager(
            provider=FakeProvider(), orchestrator=orch,
        )
        record = manager.create_run(notebook_id, "hi")
        time.sleep(0.5)  # worker is blocked; run_started+plan persisted
        events, live, done = manager.subscribe(record.run_id)
        assert done is False
        assert [e.event_type for e in events] == ["run_started", "plan"]
        # Inject live deltas straight through the worker's publish path.
        manager._publish(record, "delta", {"step_id": "s1", "content": "tok"})
        frame = live.get(timeout=5)
        assert frame["type"] == "delta" and "." in str(frame["seq"])  # N.K
        orch.release.set()
        while True:  # drain to close
            item = live.get(timeout=15)
            if item is None:
                break
        _wait_done(record.run_id)
        assert "delta" not in [e.event_type for e in store.list_events(record.run_id)]


@needs_db
class TestAdminStub:
    def test_health(self, client, clean_deps):
        api_deps.configure(
            provider=FakeProvider(),
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
        )
        r = client.get("/v1/admin/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["agents"] == [] and body["tools"] == []
        assert body["model"]
