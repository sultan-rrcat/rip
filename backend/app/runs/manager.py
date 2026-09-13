"""Run worker (Q31 REWRITE — not Athena's manager verbatim).

One run = one user message = one worker thread driving
`orchestrator.run()` to completion. The Q31 contract:

1. **Always orchestrate** — no complexity classifier, no direct shortcut.
2. **Never write `messages`** — the frontend owns user/assistant rows (Q22).
3. **Load memory before run** — `notebooks.conversation_summary` +
   `summary_message_count` + `messages` → `build_memory_context()` →
   `context=memory.as_prompt()`. A fold failure degrades to `context=None`,
   never kills the run.
4. **Persist memory after run** — updated summary/count back to `notebooks`.
5. **Emit `sources` on `rag.query`** — per completed rag step, via
   `extract_sources()` (Q32); persisted like any structural event.
6. **Event persistence (Q35)** — structural events go to `run_events` and
   are replayed from Postgres on reconnect (survives restart/eviction);
   `delta` is fanned out to live subscribers only, never INSERTed. Live
   deltas carry fractional seqs (`"<persisted>.<k>"`) which provably never
   collide with the 1-based persisted seqs, so the persisted log stays
   gap-free while every live frame still has a unique id.
7. **File artifacts (Q34)** — tool outputs written to disk; SSE `artifacts`
   carries download URLs, never inline base64.

Terminal sequence: `update_run` row first (so `GET /v1/runs/{id}` sees the
final state), then the terminal SSE event, then close. A late cancel never
overwrites a final state (`store.cancel_run` is conditional).
"""
from __future__ import annotations

import logging
import queue
import threading

from psycopg2.extras import RealDictCursor

from app.artifacts import collect_artifacts
from app.bff.envelope import EventType
from app.core.db import pg_connection
from app.orchestration.memory import build_memory_context
from app.orchestration.orchestrator import OrchestrationError
from app.services.chat import extract_sources
from app.store import runs as run_store

logger = logging.getLogger("runs.manager")

# Registry cap (completed runs included): keeps the process bounded without
# a sweeper thread; oldest live handles are dropped first. Durability is in
# Postgres — replay always reads `run_events`, never this registry.
MAX_RUNS = 500

_SENTINEL = None  # stream-end marker for subscriber queues

_TERMINAL = ("completed", "failed", "cancelled")


class RunRecord:
    """Live handle for one run: cancel flag, subscribers, done flag."""

    def __init__(self, run_id: str, notebook_id: str, message: str):
        self.run_id = run_id
        self.notebook_id = notebook_id
        self.message = message
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._done = threading.Event()
        # Delta seq state: last persisted seq + per-gap counter. Touched only
        # by the run's single worker thread — no lock needed.
        self._last_seq = 0
        self._delta_n = 0

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _close(self) -> None:
        """Terminal: wake every subscriber queue; late subscribers see done."""
        with self._lock:
            for sub in self._subscribers:
                sub.put(_SENTINEL)
            self._done.set()

    def _detach(self, sub: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def fan_out(self, event: dict) -> None:
        """Push one event dict to every live subscriber (never blocks)."""
        with self._lock:
            for sub in self._subscribers:
                sub.put(event)

    def attach(self) -> tuple[queue.Queue, bool]:
        """Attach a live subscriber; returns (queue, done_snapshot).

        The lock is held across the done-check + registration so no terminal
        event slips between a replay snapshot and going live (the SSE route
        replays from Postgres first, then calls this).
        """
        with self._lock:
            live: queue.Queue = queue.Queue()
            done_snapshot = self._done.is_set()
            if done_snapshot:
                live.put(_SENTINEL)
            else:
                self._subscribers.add(live)
            return live, done_snapshot


class RunManager:
    """Registry of live runs + the Q31 worker-thread spawner."""

    def __init__(self, *, provider, orchestrator, upload_dir: str | None = None):
        self._provider = provider
        self._orchestrator = orchestrator
        self._upload_dir = upload_dir  # None → settings.upload_dir read live
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.Lock()

    @property
    def _root(self) -> str:
        if self._upload_dir is not None:
            return self._upload_dir
        from app.core.config import settings

        return settings.upload_dir

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(str(run_id))

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._runs.values() if not r.done)

    def create_run(self, notebook_id: str, message: str) -> RunRecord:
        """Create a `pending` run row and spawn its worker (Q1 contract).

        ValueError on empty message (defense in depth — routes 422 first);
        LookupError on unknown notebook (routes map to 404).
        """
        text = (message or "").strip()
        if not text:
            raise ValueError("message must not be empty")
        notebook_id = str(notebook_id)
        if not self._notebook_exists(notebook_id):
            raise LookupError(f"Unknown notebook: {notebook_id}")
        row = run_store.create_run(notebook_id)
        run_store.update_run(row.id, status="running")
        record = RunRecord(run_id=row.id, notebook_id=notebook_id, message=text)
        with self._lock:
            while len(self._runs) >= MAX_RUNS:
                oldest = next(iter(self._runs))
                logger.info("evicting run %s (registry full)", oldest)
                del self._runs[oldest]
            self._runs[record.run_id] = record
        threading.Thread(
            target=self._worker,
            args=(record,),
            name=f"rip-run-{record.run_id[:8]}",
            daemon=True,
        ).start()
        logger.info("run started id=%s notebook=%s", record.run_id, notebook_id)
        return record

    def cancel_run(self, run_id: str) -> tuple[bool, bool]:
        """Cooperative cancel. Returns (handled, already_done).

        handled=False means unknown (no live record, no row — routes 404).
        already_done=True means the run was already terminal; a late cancel
        never overwrites a final state.
        """
        run_id = str(run_id)
        record = self.get(run_id)
        if record is not None:
            record.cancel()
        row = run_store.get_run(run_id)
        if row is None:
            return False, False
        if row.status in _TERMINAL:
            return True, True
        run_store.cancel_run(run_id)
        logger.info("run cancel requested id=%s", run_id)
        return True, False

    def subscribe(self, run_id: str):
        """Replay-from-Postgres snapshot point + live queue for SSE.

        Returns (events, queue, done). `events` are `RunEvent`s in seq order
        (structural only — deltas were never stored, Q35). Raises LookupError
        on unknown run. Works after eviction/restart: replay needs no live
        record, only the row.
        """
        run_id = str(run_id)
        if run_store.get_run(run_id) is None and self.get(run_id) is None:
            raise LookupError(f"Unknown run: {run_id}")
        events = run_store.list_events(run_id)
        record = self.get(run_id)
        if record is None:
            # Evicted or restarted run: replay is complete, end right after.
            live: queue.Queue = queue.Queue()
            live.put(_SENTINEL)
            return events, live, True
        live, done = record.attach()
        return events, live, done

    # -- worker ------------------------------------------------------------

    def _publish(
        self, record: RunRecord, type: EventType, data: dict, *, persist: bool = True
    ) -> dict:
        """Persist (unless live-only) + fan out one event; return it.

        The returned dict is `{"seq", "type", "data"}` — routes format the
        SSE frame from it, and replay rows are reshaped to the same form.
        """
        if type == "delta" or not persist:
            # Live-only: fractional seq provably outside the persisted
            # 1-based space — unique per frame, zero DB touch (Q35).
            record._delta_n += 1
            event = {
                "seq": f"{record._last_seq}.{record._delta_n}",
                "type": type,
                "data": data,
            }
            record.fan_out(event)
            return event
        stored = run_store.append_event(record.run_id, type, data)
        record._last_seq = stored.seq
        record._delta_n = 0
        event = {"seq": stored.seq, "type": type, "data": data}
        record.fan_out(event)
        return event

    def _on_event(self, record: RunRecord, d: dict) -> None:
        """Orchestrator callback: persist structural events, stream deltas."""
        type = d.get("type")
        data = {k: v for k, v in d.items() if k != "type"}
        self._publish(record, type, data)

    def _worker(self, record: RunRecord) -> None:
        try:
            self._publish(
                record,
                "run_started",
                {"run_id": record.run_id, "notebook_id": record.notebook_id,
                 "message": record.message},
            )
            context, summary_update = self._load_memory(record)
            try:
                result = self._orchestrator.run(
                    record.message,
                    record.notebook_id,
                    on_event=lambda d: self._on_event(record, d),
                    context=context,
                    cancel_event=record.cancel_event,
                )
            except OrchestrationError as e:
                if record.cancel_event.is_set():
                    self._finish_cancelled(record, str(e))
                else:
                    self._finish_failed(record, str(e))
                return

            # Q32: one `sources` event per completed rag.query step, in plan
            # order, via extract_sources() over the step's retrieved chunks.
            for step in result.step_results or []:
                if (
                    getattr(step.agent_id, "value", step.agent_id) == "rag.query"
                    and getattr(step.status, "value", step.status) == "success"
                    and isinstance(getattr(step, "data", None), dict)
                    and isinstance(step.data.get("results"), list)
                ):
                    sources = extract_sources({"results": step.data["results"]})
                    if sources:
                        self._publish(
                            record, "sources",
                            {"step_id": step.step_id, "sources": sources},
                        )

            # Q34: tool outputs to disk; SSE carries download URLs only.
            artifacts = collect_artifacts(
                result.step_results or [],
                upload_dir=self._root,
                notebook_id=record.notebook_id,
                run_id=record.run_id,
            )
            if artifacts:
                self._publish(record, "artifacts", {"artifacts": artifacts})

            self._publish(
                record,
                "summary",
                {
                    "content": result.summary,
                    "status": result.status,
                    "plan_incomplete": result.plan_incomplete,
                    "conflicts": result.conflicts,
                    "needs_clarification": result.needs_clarification,
                },
            )
            terminal = "completed" if result.status in ("success", "partial") else "failed"
            run_store.update_run(
                record.run_id,
                status=terminal,
                goal=result.goal,
                plan={"plan_id": result.plan_id, "goal": result.goal},
                result={
                    "summary": result.summary,
                    "status": result.status,
                    "plan_incomplete": result.plan_incomplete,
                    "conflicts": result.conflicts,
                    "needs_clarification": result.needs_clarification,
                },
            )
            self._persist_memory(record, summary_update)
            self._publish(record, "run_completed",
                           {"status": result.status, "run_id": record.run_id})
        except Exception as e:  # noqa: BLE001 - fail-honest terminal event
            logger.exception("run %s crashed", record.run_id)
            self._finish_failed(record, str(e))
        finally:
            record._close()

    def _finish_failed(self, record: RunRecord, message: str) -> None:
        try:
            run_store.update_run(record.run_id, status="failed")
        except Exception:  # noqa: BLE001 - terminal event matters more
            logger.exception("run %s: failed-state persist failed", record.run_id)
        try:
            self._publish(record, "error", {"message": message})
        except Exception:  # noqa: BLE001 - closing still matters
            logger.exception("run %s: error event persist failed", record.run_id)

    def _finish_cancelled(self, record: RunRecord, reason: str) -> None:
        try:
            run_store.cancel_run(record.run_id)
        except Exception:  # noqa: BLE001 - terminal event matters more
            logger.exception("run %s: cancelled-state persist failed", record.run_id)
        try:
            self._publish(record, "cancelled", {"reason": reason})
        except Exception:  # noqa: BLE001 - closing still matters
            logger.exception("run %s: cancelled event persist failed", record.run_id)

    # -- memory (Q31 steps 3-4) --------------------------------------------

    def _notebook_exists(self, notebook_id: str) -> bool:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM notebooks WHERE notebook_id = %s", (notebook_id,)
                )
                return cur.fetchone() is not None

    def _load_memory(self, record: RunRecord) -> tuple[str | None, tuple | None]:
        """Read summary + messages; return (prompt context, persist update).

        The update is `(new_summary, new_count, old_summary, old_count)` for
        `_persist_memory`, or None when folding failed / nothing changed.
        The user's just-sent message is NOT appended here: the frontend POSTs
        it before creating the run, and re-adding it would double-count on
        the race where that POST already landed.
        """
        with pg_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT conversation_summary, summary_message_count "
                    "FROM notebooks WHERE notebook_id = %s",
                    (record.notebook_id,),
                )
                nb = cur.fetchone()
                cur.execute(
                    "SELECT role, text FROM messages WHERE notebook_id = %s "
                    "ORDER BY created_at, message_id",
                    (record.notebook_id,),
                )
                rows = cur.fetchall()
        stored = nb["conversation_summary"] if nb else None
        folded = int(nb["summary_message_count"] or 0) if nb else 0
        messages = [{"role": r["role"], "content": r["text"]} for r in rows]
        try:
            memory, new_summary, new_count = build_memory_context(
                self._provider, stored, messages, folded_count=folded
            )
        except Exception:  # noqa: BLE001 - memory must never kill a run
            logger.exception("run %s: memory fold failed, continuing bare", record.run_id)
            return None, None
        prompt = memory.as_prompt() or None
        update = None
        if new_summary != stored or new_count != folded:
            update = (new_summary, new_count)
        return prompt, update

    def _persist_memory(self, record: RunRecord, update: tuple | None) -> None:
        if not update:
            return
        new_summary, new_count = update
        try:
            with pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE notebooks SET conversation_summary = %s, "
                        "summary_message_count = %s WHERE notebook_id = %s",
                        (new_summary, new_count, record.notebook_id),
                    )
        except Exception:  # noqa: BLE001 - memory persist is best-effort
            logger.exception("run %s: memory persist failed", record.run_id)


_manager: RunManager | None = None


def get_run_manager() -> RunManager:
    """Process-wide singleton — runs must be addressable across requests."""
    global _manager
    if _manager is None:
        from app.api.deps import get_model_provider, get_orchestrator

        _manager = RunManager(
            provider=get_model_provider(), orchestrator=get_orchestrator()
        )
    return _manager


def set_run_manager(manager: RunManager | None) -> None:
    """Install/replace the process manager (main.py lifespan; tests)."""
    global _manager
    _manager = manager


__all__ = ["RunManager", "RunRecord", "get_run_manager", "set_run_manager"]
