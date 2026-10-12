"""Studio panel routes — Convert + Quiz MVP (direct-builder via synthetic messages).

Each POST builds a synthetic rule-routed message and creates a normal run
through the existing RunManager worker (Router → Builders → Validator →
Engine → Aggregator). The studio_jobs row links job_id → run_id; status and
artifacts are derived live from `runs` / `run_events` so history survives
reload without duplicating the run state machine.

Why synthetic messages instead of direct builder calls: the rule
pre-filter (`router._rule_pre_filter`) already classifies
`Convert "f.pdf" to docx` → CONVERT_ONE, `Convert all documents to pdf` →
CONVERT_ALL, and `Generate N <kind> quiz questions ...` → QUIZ with zero
LLM calls. Reusing it means zero orchestrator changes and identical
validation, artifacts, SSE replay, cancel, and Langfuse traces.

Owner-scoped: foreign notebook/job ids are 404, never 403.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.api.deps import get_run_manager
from app.core.db import pg_connection
from app.routes.auth import UserResponse, get_current_user
from app.store import studio_jobs as job_store

logger = logging.getLogger("routes.studio")

router = APIRouter()

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_QUIZ_KINDS = ("mcq", "msq", "truefalse")
_CONVERT_FORMATS = ("md", "docx", "pdf")


def _validate_uuid(value: str, field: str) -> None:
    if not _UUID_RE.match(value or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")


def _verify_notebook_ownership(notebook_id: str, user_id: str) -> None:
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM notebooks WHERE notebook_id = %s AND owner_id = %s",
            (notebook_id, user_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Notebook not found")


def _verify_job_ownership(job_id: str, user_id: str) -> tuple[str, str]:
    """Return (notebook_id, run_id) or raise 404 (no 403 leak)."""
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT j.notebook_id, j.run_id FROM studio_jobs j
            JOIN notebooks n ON n.notebook_id = j.notebook_id
            WHERE j.job_id = %s AND n.owner_id = %s
            """,
            (job_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Studio job not found")
    return str(row[0]), str(row[1])


def _owned_file_name(notebook_id: str, file_id: str, user_id: str) -> str:
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.file_name FROM files f
            JOIN notebooks n ON n.notebook_id = f.notebook_id
            WHERE f.file_id = %s AND f.notebook_id = %s AND n.owner_id = %s
            """,
            (file_id, notebook_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    return str(row[0] or "")


class QuizRequest(BaseModel):
    count: int = 10
    kind: str = "mcq"

    @field_validator("count")
    @classmethod
    def _count_bounds(cls, v: int) -> int:
        if not isinstance(v, int) or not 2 <= v <= 50:
            raise ValueError("count must be an integer 2..50")
        return v

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        if str(v).lower() not in _QUIZ_KINDS:
            raise ValueError(f"kind must be one of {list(_QUIZ_KINDS)}")
        return str(v).lower()


class ConvertRequest(BaseModel):
    file_id: str | None = None
    target_format: str

    @field_validator("target_format")
    @classmethod
    def _format_known(cls, v: str) -> str:
        if str(v).lower() not in _CONVERT_FORMATS:
            raise ValueError(f"target_format must be one of {list(_CONVERT_FORMATS)}")
        return str(v).lower()


class JobResponse(BaseModel):
    job_id: UUID
    notebook_id: UUID
    kind: str
    params: dict[str, Any]
    run_id: UUID
    status: str
    created_at: str | None = None
    artifacts: list[dict[str, Any]] = []


class CancelJobResponse(BaseModel):
    job_id: str
    run_id: str
    status: str
    already_done: bool


def _quiz_message(count: int, kind: str) -> str:
    # Rule-routed: _QUIZ_RE (quiz|mcq|msq...) + _QUIZ_VERB_RE (generate).
    # parse_quiz_params() picks count + kind back out of this text, and
    # router slots stay unset — builder parse is the single source of truth.
    return f"Generate {count} {kind} quiz questions covering my documents"


def _to_response(job: job_store.StudioJob) -> JobResponse:
    return JobResponse(
        job_id=UUID(job.job_id),
        notebook_id=UUID(job.notebook_id),
        kind=job.kind,
        params=job.params,
        run_id=UUID(job.run_id),
        status=job.status,
        created_at=job.created_at.isoformat() if job.created_at else None,
        artifacts=job.artifacts,
    )


@router.post(
    "/api/notebooks/{id}/studio/quiz", response_model=JobResponse, status_code=202
)
def create_quiz_job(
    id: str,
    body: QuizRequest,
    manager=Depends(get_run_manager),  # noqa: B008 - FastAPI Depends-in-default is canonical
    user: UserResponse = Depends(get_current_user),  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    _validate_uuid(id, "notebook_id")
    _verify_notebook_ownership(id, user.user_id)
    message = _quiz_message(body.count, body.kind)
    try:
        record = manager.create_run(id, message)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(
            status_code=429, detail=str(e), headers={"Retry-After": "30"}
        ) from e
    job = job_store.create_job(
        id, "quiz", {"count": body.count, "kind": body.kind}, record.run_id
    )
    job.status = "running"
    logger.info("studio quiz job=%s run=%s notebook=%s", job.job_id, record.run_id, id)
    return _to_response(job)


@router.post(
    "/api/notebooks/{id}/studio/convert", response_model=JobResponse, status_code=202
)
def create_convert_job(
    id: str,
    body: ConvertRequest,
    manager=Depends(get_run_manager),  # noqa: B008 - FastAPI Depends-in-default is canonical
    user: UserResponse = Depends(get_current_user),  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    _validate_uuid(id, "notebook_id")
    _verify_notebook_ownership(id, user.user_id)
    fmt = body.target_format
    if body.file_id == "*" or body.file_id is None:
        # Convert-all: _ALL_DOCS_RE ("all ... documents") → CONVERT_ALL.
        message = f"Convert all documents to {fmt}"
        params: dict[str, Any] = {"file_id": "*", "target_format": fmt}
    else:
        _validate_uuid(body.file_id, "file_id")
        # Convert-one: quoted filename → file_hint, format token → slot.
        # The builder resolves the hint against the snapshot; ambiguous or
        # missing names fall through to ReAct fail-honest (never invented).
        file_name = _owned_file_name(id, body.file_id, user.user_id)
        message = f'Convert "{file_name}" to {fmt}'
        params = {
            "file_id": body.file_id,
            "file_name": file_name,
            "target_format": fmt,
        }
    try:
        record = manager.create_run(id, message)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(
            status_code=429, detail=str(e), headers={"Retry-After": "30"}
        ) from e
    job = job_store.create_job(id, "convert", params, record.run_id)
    job.status = "running"
    logger.info(
        "studio convert job=%s run=%s notebook=%s fmt=%s",
        job.job_id,
        record.run_id,
        id,
        fmt,
    )
    return _to_response(job)


@router.get("/api/notebooks/{id}/studio/jobs", response_model=list[JobResponse])
def list_studio_jobs(
    id: str,
    user: UserResponse = Depends(get_current_user),  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    _validate_uuid(id, "notebook_id")
    _verify_notebook_ownership(id, user.user_id)
    return [_to_response(j) for j in job_store.list_jobs(id)]


@router.get("/api/notebooks/{id}/studio/jobs/{job_id}", response_model=JobResponse)
def get_studio_job(
    id: str,
    job_id: str,
    user: UserResponse = Depends(get_current_user),  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    _validate_uuid(id, "notebook_id")
    _validate_uuid(job_id, "job_id")
    _verify_notebook_ownership(id, user.user_id)
    notebook_id, _ = _verify_job_ownership(job_id, user.user_id)
    if notebook_id != id:
        raise HTTPException(status_code=404, detail="Studio job not found")
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Studio job not found")
    return _to_response(job)


@router.post(
    "/api/notebooks/{id}/studio/jobs/{job_id}/cancel", response_model=CancelJobResponse
)
def cancel_studio_job(
    id: str,
    job_id: str,
    manager=Depends(get_run_manager),  # noqa: B008 - FastAPI Depends-in-default is canonical
    user: UserResponse = Depends(get_current_user),  # noqa: B008 - FastAPI Depends-in-default is canonical
):
    _validate_uuid(id, "notebook_id")
    _validate_uuid(job_id, "job_id")
    _verify_notebook_ownership(id, user.user_id)
    notebook_id, run_id = _verify_job_ownership(job_id, user.user_id)
    if notebook_id != id:
        raise HTTPException(status_code=404, detail="Studio job not found")
    handled, already_done = manager.cancel_run(run_id)
    if not handled:
        raise HTTPException(status_code=404, detail="Run not found")
    from app.store import runs as run_store

    row = run_store.get_run(run_id)
    status = row.status if row else "unknown"
    return CancelJobResponse(
        job_id=job_id, run_id=run_id, status=status, already_done=already_done
    )
