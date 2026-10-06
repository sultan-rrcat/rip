"""Phase 3.3 — runs store: Postgres CRUD + Q35 replay.

Needs a live Postgres with schema.sql applied (compose `postgres`
service; from the host: DB_HOST=localhost DB_PORT=5433 DB_NAME=rip
DB_USER=rip DB_PASSWORD=rippass). Skips when unreachable. Run with
``pytest backend/tests/test_runs_store.py -q --noconftest`` until the
torch env is repaired — the shared conftest imports app.main, which needs
sentence_transformers.
"""

from __future__ import annotations

import pytest

try:
    import psycopg2
    from app.core.db import pg_connection
    from app.store import runs as store
    from psycopg2 import errors as _pg_errors

    _IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001 - import probe; pragma: no cover
    psycopg2 = None  # type: ignore[assignment]
    _pg_errors = None  # type: ignore[assignment]
    pg_connection = None  # type: ignore[assignment]
    store = None  # type: ignore[assignment]
    _IMPORT_ERROR = e


def _db_up() -> bool:
    if pg_connection is None:
        return False
    try:
        with pg_connection():
            pass
        return True
    except Exception:  # noqa: BLE001 - any connect failure means "down"
        return False


needs_db = pytest.mark.skipif(
    _IMPORT_ERROR is not None or not _db_up(),
    reason=f"postgres unreachable ({_IMPORT_ERROR or 'connect failed'})",
)


@pytest.fixture()
def notebook_id():
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notebooks (notebook_name) VALUES (%s) "
            "RETURNING notebook_id",
            ("store-test",),
        )
        nb = str(cur.fetchone()[0])
    yield nb
    with pg_connection() as conn, conn.cursor() as cur:
        # CASCADE wipes this notebook's files/messages/runs/run_events.
        cur.execute("DELETE FROM notebooks WHERE notebook_id = %s", (nb,))


@needs_db
class TestRuns:
    def test_create_run_pending(self, notebook_id):
        run = store.create_run(notebook_id)
        assert run.status == "pending"
        assert run.notebook_id == notebook_id
        assert run.goal is None and run.plan is None
        assert run.created_at is not None

    def test_create_run_unknown_notebook_fails(self):
        with pytest.raises(_pg_errors.ForeignKeyViolation):
            store.create_run("00000000-0000-0000-0000-000000000000")

    def test_get_run_roundtrip(self, notebook_id):
        created = store.create_run(notebook_id)
        fetched = store.get_run(created.id)
        assert fetched is not None and fetched.id == created.id
        assert fetched.status == "pending"

    def test_get_run_unknown_returns_none(self):
        assert store.get_run("00000000-0000-0000-0000-000000000000") is None

    def test_update_run_fields(self, notebook_id):
        run = store.create_run(notebook_id)
        updated = store.update_run(
            run.id, status="running", goal="answer things",
            plan={"steps": []}, result={"summary": "x"},
        )
        assert updated.status == "running"
        assert updated.goal == "answer things"
        assert updated.plan == {"steps": []} and updated.result == {"summary": "x"}
        assert store.get_run(run.id).status == "running"

    def test_update_run_bad_status(self, notebook_id):
        run = store.create_run(notebook_id)
        with pytest.raises(ValueError):
            store.update_run(run.id, status="exploding")

    def test_update_run_unknown_raises(self):
        with pytest.raises(KeyError):
            store.update_run("00000000-0000-0000-0000-000000000000", status="failed")

    def test_cancel_pending_run(self, notebook_id):
        run = store.create_run(notebook_id)
        assert store.cancel_run(run.id) is True
        assert store.get_run(run.id).status == "cancelled"

    def test_cancel_terminal_run_is_noop(self, notebook_id):
        run = store.create_run(notebook_id)
        store.update_run(run.id, status="completed")
        assert store.cancel_run(run.id) is False
        assert store.get_run(run.id).status == "completed"

    def test_cancel_unknown_run_is_noop(self):
        assert store.cancel_run("00000000-0000-0000-0000-000000000000") is False

    def test_list_runs_isolated_and_ordered(self, notebook_id):
        first = store.create_run(notebook_id)
        second = store.create_run(notebook_id)
        ids = [r.id for r in store.list_runs(notebook_id)]
        assert ids == [first.id, second.id]
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notebooks (notebook_name) VALUES (%s) "
                "RETURNING notebook_id",
                ("store-other",),
            )
            other = str(cur.fetchone()[0])
        try:
            assert [r.id for r in store.list_runs(other)] == []
        finally:
            with pg_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM notebooks WHERE notebook_id = %s", (other,)
                )


@needs_db
class TestRunEvents:
    def test_append_and_replay_in_order(self, notebook_id):
        run = store.create_run(notebook_id)
        e1 = store.append_event(run.id, "run_started", {"run_id": run.id})
        e2 = store.append_event(run.id, "plan", {"goal": "g", "steps": []})
        e3 = store.append_event(
            run.id, "step_completed",
            {"step_id": "1", "status": "success", "output": "hi"},
        )
        assert [e.seq for e in (e1, e2, e3)] == [1, 2, 3]
        replayed = store.list_events(run.id)
        assert [e.event_type for e in replayed] == [
            "run_started", "plan", "step_completed",
        ]
        assert [e.seq for e in replayed] == [1, 2, 3]
        assert replayed[2].payload["output"] == "hi"

    def test_delta_not_persisted_no_gap(self, notebook_id):
        run = store.create_run(notebook_id)
        store.append_event(run.id, "step_started", {"step_id": "1"})
        assert store.append_event(run.id, "delta", {"content": "tok"}) is None
        store.append_event(run.id, "step_completed", {"step_id": "1"})
        replayed = store.list_events(run.id)
        assert [e.event_type for e in replayed] == ["step_started", "step_completed"]
        assert [e.seq for e in replayed] == [1, 2]  # skipped delta consumes no seq

    def test_append_unknown_run_raises(self):
        with pytest.raises(KeyError):
            store.append_event(
                "00000000-0000-0000-0000-000000000000", "plan", {}
            )

    def test_events_visible_across_connections(self, notebook_id):
        # Every store call opens its own connection: create + append here,
        # read back through fresh connections — durability, not buffers.
        run = store.create_run(notebook_id)
        store.append_event(run.id, "summary", {"content": "answer"})
        assert store.get_run(run.id).status == "pending"
        replayed = store.list_events(run.id)
        assert len(replayed) == 1 and replayed[0].payload["content"] == "answer"


class TestPublishNeverRaises:
    """Event persistence is bookkeeping; it must never abort a run.

    Trace eb2d8fd3: the user deleted the notebook while the LLM call was in
    flight, and ON DELETE CASCADE removed the `runs` row. The next
    append_event raised KeyError, which unwound the engine's `plan` node and
    surfaced as a bogus run failure ("Unknown run"). Publishing must degrade
    to live-only streaming instead.
    """

    def _manager_and_record(self):
        from app.runs.manager import RunManager, RunRecord

        manager = RunManager(provider=None, orchestrator=None)
        record = RunRecord(
            run_id="11111111-1111-1111-1111-111111111111",
            notebook_id="nb-1",
            message="hi",
        )
        return manager, record

    def test_vanished_run_row_degrades_to_live_only(self, monkeypatch):
        from app.runs import manager as manager_mod

        calls = {"n": 0}

        def _gone(run_id, event_type, payload=None):
            calls["n"] += 1
            raise KeyError(f"Unknown run: {run_id}")

        monkeypatch.setattr(manager_mod.run_store, "append_event", _gone)
        manager, record = self._manager_and_record()
        record.attach()  # registers one subscriber queue

        first = manager._publish(record, "plan", {"plan_id": "p"})
        assert first["type"] == "plan"
        assert record.orphaned is True
        # Still delivered live.
        frame = record._subscribers.copy().pop().get(timeout=5)
        assert frame["type"] == "plan"
        # Once orphaned, persistence is not retried (no log spam / no DB churn).
        second = manager._publish(record, "summary", {"content": "x"})
        assert calls["n"] == 1
        assert second["seq"] != first["seq"]

    def test_healthy_path_still_persists(self, monkeypatch):
        from app.runs import manager as manager_mod

        class _Stored:
            seq = 7

        seen = []

        def _ok(run_id, event_type, payload=None):
            seen.append(event_type)
            return _Stored()

        monkeypatch.setattr(manager_mod.run_store, "append_event", _ok)
        manager, record = self._manager_and_record()
        event = manager._publish(record, "plan", {"plan_id": "p"})
        assert event["seq"] == 7
        assert record.orphaned is False
        assert seen == ["plan"]
