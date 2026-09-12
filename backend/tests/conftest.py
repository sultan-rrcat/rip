"""Shared fixtures for backend integration tests.

These tests run against the REAL stack — no mocks, no fakes:

- Real PostgreSQL (same database as the app, per project decision).
  Each test works inside its own notebook and the fixture teardown deletes
  it; Postgres CASCADE removes files/messages/embeddings, and uploaded
  files on disk are removed explicitly.
- Real ML models: the app lifespan loads the actual ``VectorRAG``
  (BGE-M3 embeddings + BGE reranker) once per test session.
- Real HTTP APIs via ``TestClient``.
- Real Ollama endpoint for the prompt tests (skipped if unreachable).
"""

import os
import shutil
import sys
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

# Make `backend/` importable when pytest runs from the repo root.
# After Phase 1.1 the import root is `backend/app/` (run from `backend/`
# as `uvicorn app.main:app`), so BACKEND_DIR must be on sys.path for
# `from app.*` imports to resolve.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.core import config  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.db import pg_connection  # noqa: E402


def _apply_schema():
    """Apply schema.sql idempotently (CREATE TABLE/INDEX IF NOT EXISTS)."""
    schema_path = os.path.join(BACKEND_DIR, "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)


@pytest.fixture(scope="session")
def client():
    """TestClient with lifespan executed: real VectorRAG models loaded once."""
    _apply_schema()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    # Deterministic native teardown: release torch models and empty the
    # CUDA cache before interpreter shutdown. Without this, background
    # native threads (torch/CUDA workers) can race module GC on Windows
    # and dump an access-violation traceback after the run is green.
    try:
        import gc

        import torch

        app.state.rag = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception:
        pass


@pytest.fixture()
def test_notebook(client):
    """A fresh notebook per test; fully cleaned up afterwards."""
    name = f"pytest-{uuid.uuid4().hex[:8]}"
    response = client.post("/api/notebooks", json={"notebook_name": name})
    assert response.status_code == 200
    notebook_id = response.json()["notebook_id"]
    yield notebook_id
    # Teardown: DB cascade handles files/messages/embeddings.
    client.delete(f"/api/notebooks/{notebook_id}")
    notebook_dir = os.path.join(config.UPLOAD_DIR, notebook_id)
    shutil.rmtree(notebook_dir, ignore_errors=True)


def llm_available() -> bool:
    """Probe the real Ollama endpoint; prompt tests skip when it is down."""
    try:
        base_url = Settings().ollama_base_url.rstrip("/")
    except Exception:
        return False
    if not base_url:
        return False
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=10, trust_env=False)
        return response.status_code < 500
    except Exception:
        return False


needs_llm = pytest.mark.skipif(
    not llm_available(), reason="Ollama endpoint unavailable"
)


def wait_for_file_status(client, notebook_id, file_id, timeout=180, interval=3):
    """Poll the files list until the ingestion reaches a terminal status."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/notebooks/{notebook_id}/files")
        assert response.status_code == 200
        files = {f["id"]: f for f in response.json()}
        assert file_id in files, f"file {file_id} vanished during processing"
        last = files[file_id]["status"]
        if last in ("ready", "error"):
            return last
        time.sleep(interval)
    raise TimeoutError(
        f"file {file_id} stuck in status {last!r} after {timeout}s"
    )
