"""Run lifecycle routes (Phase 4.1).

Q1 locked contract, copied verbatim in shape:
- `POST /v1/runs` body is exactly `{notebook_id: UUID, message: str}` →
  `202 {run_id}` bare JSON. No auth/quota/tenant, no BFF envelope on `/v1/*`.
- Errors: `404` unknown `notebook_id`, `422` empty message.
- `GET /v1/runs/{id}` — run detail (Postgres row is truth).
- `GET /v1/runs/{id}/events` — SSE; replays persisted `run_events ORDER BY
  seq` (structural + final text, no deltas — Q35) with `id:<seq>`, then
  streams live. Frontend dedupes by `seq`. No `?last_event_id=` in v1.
- `POST /v1/runs/{id}/cancel` — cooperative cancel (idempotent; a late
  cancel never overwrites a final state).
- `GET /v1/runs/{id}/artifacts/{artifact_id}` — Q34 file download.

Full `/v1/...` paths inline (RIP convention — routers mount unprefixed).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from app.api.deps import get_run_manager
from app.artifacts import resolve_artifact
from app.core.config import settings
from app.store import runs as run_store

logger = logging.getLogger("api.runs")

router = APIRouter()


class CreateRunRequest(BaseModel):
    notebook_id: UUID
    message: str  # min_length=1 after strip; 422 on empty

    @field_validator("message")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be empty")
        return v


class CreateRunResponse(BaseModel):
    run_id: UUID  # bare JSON, NO BFF envelope on /v1/*


class RunDetail(BaseModel):
    run_id: str
    notebook_id: str
    status: str
    goal: str | None = None


class CancelRunResponse(BaseModel):
    run_id: str
    status: str
    already_done: bool


def _frame(seq: Any, type: str, run_id: str, data: dict) -> str:
    """One SSE frame: `id: <seq>` + flat JSON payload (type/run_id/seq up)."""
    payload = {"type": type, "run_id": run_id, "seq": seq, **(data or {})}
    return f"id: {seq}\ndata: {json.dumps(payload)}\n\n"


@router.post("/v1/runs", response_model=CreateRunResponse, status_code=202)
def create_run(
    body: CreateRunRequest,
    manager=Depends(get_run_manager),  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    try:
        record = manager.create_run(str(body.notebook_id), body.message)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    logger.info("created run id=%s notebook=%s", record.run_id, record.notebook_id)
    return CreateRunResponse(run_id=UUID(record.run_id))


@router.get("/v1/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str):
    row = run_store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunDetail(
        run_id=row.id, notebook_id=row.notebook_id, status=row.status, goal=row.goal
    )


@router.post("/v1/runs/{run_id}/cancel", response_model=CancelRunResponse)
def cancel_run(
    run_id: str, manager=Depends(get_run_manager)  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    handled, already_done = manager.cancel_run(run_id)
    if not handled:
        raise HTTPException(status_code=404, detail="Run not found")
    row = run_store.get_run(run_id)
    status = row.status if row else "unknown"
    return CancelRunResponse(run_id=run_id, status=status, already_done=already_done)


@router.get("/v1/runs/{run_id}/events")
def run_events(
    run_id: str, manager=Depends(get_run_manager)  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    try:
        events, live, _done = manager.subscribe(run_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Run not found") from None

    def stream():
        for ev in events:
            yield _frame(ev.seq, ev.event_type, run_id, ev.payload or {})
        while True:
            item = live.get()
            if item is None:  # sentinel: run closed
                return
            yield _frame(item["seq"], item["type"], run_id, item.get("data") or {})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/v1/runs/{run_id}/artifacts/{artifact_id}")
def download_artifact(run_id: str, artifact_id: str):
    row = run_store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    resolved = resolve_artifact(
        upload_dir=settings.upload_dir,
        notebook_id=row.notebook_id,
        run_id=run_id,
        artifact_id=artifact_id,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path, filename, mime = resolved
    return FileResponse(path, filename=filename, media_type=mime)
