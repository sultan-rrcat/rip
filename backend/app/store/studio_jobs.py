"""Studio jobs store — thin wrapper over `runs` for the Studio panel.

Each studio job maps 1:1 to a `runs` row created by the studio routes with
a synthetic rule-routed message. Status is derived live from `runs.status`
(no duplicated state machine); artifacts are collected from the run's
persisted `artifacts` SSE events so panel history survives reload.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from psycopg2.extras import Json, RealDictCursor

from app.core.db import pg_connection

logger = logging.getLogger("store.studio_jobs")

VALID_KINDS = ("quiz", "convert")


class StudioJob:
    """One studio job row joined to its run status + artifacts."""

    def __init__(
        self,
        job_id: str,
        notebook_id: str,
        kind: str,
        params: dict | None = None,
        run_id: str = "",
        status: str = "pending",
        created_at: datetime | None = None,
        artifacts: list[dict] | None = None,
    ):
        self.job_id = str(job_id)
        self.notebook_id = str(notebook_id)
        self.kind = kind
        self.params = params if params is not None else {}
        self.run_id = str(run_id)
        self.status = status
        self.created_at = created_at
        self.artifacts = artifacts if artifacts is not None else []


def create_job(notebook_id: str, kind: str, params: dict, run_id: str) -> StudioJob:
    """Insert a studio job; KeyError propagates on FK violation (bad ids)."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind {kind!r} must be one of {', '.join(VALID_KINDS)}")
    with pg_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
                INSERT INTO studio_jobs (notebook_id, kind, params, run_id)
                VALUES (%s, %s, %s, %s)
                RETURNING job_id, notebook_id, kind, params, run_id, created_at
                """,
            (str(notebook_id), kind, Json(params or {}), str(run_id)),
        )
        row = cur.fetchone()
    logger.info("studio job created id=%s kind=%s run=%s", row["job_id"], kind, run_id)
    return StudioJob(
        job_id=row["job_id"],
        notebook_id=row["notebook_id"],
        kind=row["kind"],
        params=dict(row["params"] or {}),
        run_id=row["run_id"],
        status="running",
        created_at=row["created_at"],
    )


def _artifacts_for_run(run_id: str) -> list[dict]:
    """Merge all persisted `artifacts` event payloads for a run."""
    with pg_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
                SELECT payload FROM run_events
                WHERE run_id = %s AND event_type = 'artifacts'
                ORDER BY seq
                """,
            (str(run_id),),
        )
        rows = cur.fetchall()
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        payload = r["payload"] or {}
        for a in payload.get("artifacts", []) or []:
            aid = str(a.get("artifact_id", ""))
            if aid and aid not in seen:
                seen.add(aid)
                out.append(
                    {
                        "artifact_id": aid,
                        "kind": a.get("kind", ""),
                        "filename": a.get("filename", ""),
                        "url": a.get("url", ""),
                    }
                )
    return out


def get_job(job_id: str) -> StudioJob | None:
    """Fetch one job with live run status + artifacts; None when unknown."""
    with pg_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
                SELECT j.job_id, j.notebook_id, j.kind, j.params, j.run_id,
                       j.created_at, r.status
                FROM studio_jobs j
                JOIN runs r ON r.id = j.run_id
                WHERE j.job_id = %s
                """,
            (str(job_id),),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return StudioJob(
        job_id=row["job_id"],
        notebook_id=row["notebook_id"],
        kind=row["kind"],
        params=dict(row["params"] or {}),
        run_id=row["run_id"],
        status=row["status"],
        created_at=row["created_at"],
        artifacts=_artifacts_for_run(str(row["run_id"])),
    )


def list_jobs(notebook_id: str, limit: int = 50) -> list[StudioJob]:
    """Newest-first jobs of a notebook, each with live status + artifacts."""
    with pg_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
                SELECT j.job_id, j.notebook_id, j.kind, j.params, j.run_id,
                       j.created_at, r.status
                FROM studio_jobs j
                JOIN runs r ON r.id = j.run_id
                WHERE j.notebook_id = %s
                ORDER BY j.created_at DESC, j.job_id DESC
                LIMIT %s
                """,
            (str(notebook_id), int(limit)),
        )
        rows = cur.fetchall()
    jobs: list[StudioJob] = []
    for row in rows:
        jobs.append(
            StudioJob(
                job_id=row["job_id"],
                notebook_id=row["notebook_id"],
                kind=row["kind"],
                params=dict(row["params"] or {}),
                run_id=row["run_id"],
                status=row["status"],
                created_at=row["created_at"],
                artifacts=_artifacts_for_run(str(row["run_id"])),
            )
        )
    return jobs


def job_to_dict(job: StudioJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "notebook_id": job.notebook_id,
        "kind": job.kind,
        "params": job.params,
        "run_id": job.run_id,
        "status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "artifacts": job.artifacts,
    }
