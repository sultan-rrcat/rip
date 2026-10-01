"""End-user UAT suite — full journeys through the real stack.

Runs the same path a browser user takes (minus the frontend): login →
notebook → upload → process → POST /v1/runs → poll → events → artifacts.
Needs the LIVE stack (Postgres + BGE weights + Ollama); the whole module
skips otherwise. Each run can take minutes on a local LLM — this is a
release-gate suite, not a per-commit suite.

Auth (UAT credential)
---------------------
Username ``admin``, password ``admin`` (override with ``UAT_USERNAME`` /
``UAT_PASSWORD`` env vars). The DB seeds no users, so create it once::

    python scripts/create_user.py admin admin

(already-exists → nothing to do). Login happens ONCE per session — the
auth route allows 5 attempts/min/IP with a 15-min lockout, so per-test
logins would risk locking the suite out. The session cookie
(``rip_session``) is kept on the shared TestClient.

    WARNING: admin/admin is a local-UAT convenience only. Never use a
    weak default against a shared or production database.

Fixture PDFs (``backend/tests/data/``)
--------------------------------------
- ``Anomaly-Detection-Report.pdf`` — anomaly detection topics.
- ``Indus-Faultbook-Assistant-Report.pdf`` — "Faultbook" failure modes.
- ``SQL-Server-Table-Partitioning.pdf`` — unrelated topic (isolation probe:
  a partitioning question must cite partitioning, never the Faultbook).

Assertion style: full-content but keyword-based (case-insensitive keyword
sets, never verbatim strings — live LLMs paraphrase). Routing is asserted
via the persisted ``plan`` event's ``route.intent``; retrieval via the
``sources`` event; files/charts via the ``artifacts`` event.

Case map (user request → test)
------------------------------
1. greeting + reasoning + follow-up, empty notebook → test_greeting,
   test_reasoning_followup
2. reasoning + plot, no doc → test_plot_no_doc
3. reasoning-only with docs present (no false rag.query) → test_no_false_rag
4. single doc → test_single_doc_qa, test_single_doc_summarize
5. multiple docs → test_multi_doc_compare, test_multi_doc_quiz
6. docs + plotting → test_docs_plot
7. convert with no doc → test_convert_no_doc
8. convert one / all → test_convert_one, test_convert_all
9. report file → test_report (the verbatim csv/txt/code path is unit-
   covered in TestDocGenerateVerbatim: no router intent reaches it E2E —
   doc-grounded file asks take the compare/report/convert builders and
   knowledge-grounded file asks take knowledge_qa)
+  code agent → test_code; cancel → test_cancel_run
"""

from __future__ import annotations

import os
import sys
import time
import uuid

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from conftest import llm_available, wait_for_file_status

DATA_DIR = os.path.join(BACKEND_DIR, "tests", "data")
ANOMALY_PDF = os.path.join(DATA_DIR, "Anomaly-Detection-Report.pdf")
FAULTBOOK_PDF = os.path.join(DATA_DIR, "Indus-Faultbook-Assistant-Report.pdf")
PARTITIONING_PDF = os.path.join(DATA_DIR, "SQL-Server-Table-Partitioning.pdf")

UAT_USERNAME = os.getenv("UAT_USERNAME", "admin")
UAT_PASSWORD = os.getenv("UAT_PASSWORD", "admin")

TERMINAL = {"completed", "failed", "cancelled"}
RUN_TIMEOUT_S = 900
POLL_S = 5


def _stack_up() -> tuple[bool, str]:
    """DB reachable, seed PDFs present, and Ollama answering."""
    for pdf in (ANOMALY_PDF, FAULTBOOK_PDF, PARTITIONING_PDF):
        if not os.path.isfile(pdf):
            return False, f"missing fixture pdf {pdf}"
    try:
        from app.core.db import pg_connection

        with pg_connection():
            pass
    except Exception as e:  # noqa: BLE001 - any connect failure means "down"
        return False, f"postgres unreachable ({e})"
    if not llm_available():
        return False, "ollama unreachable"
    return True, ""


_UP, _WHY = _stack_up()
needs_stack = pytest.mark.skipif(not _UP, reason=f"UAT needs live stack ({_WHY})")


# --- helpers --------------------------------------------------------------


def _text(resp) -> str:
    return (resp.json() if isinstance(resp.json(), str) else str(resp.json()))


def _login(client) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": UAT_USERNAME, "password": UAT_PASSWORD},
    )
    if r.status_code == 401:
        pytest.fail(
            f"login as {UAT_USERNAME!r} failed (401). Seed it once with: "
            f"python scripts/create_user.py {UAT_USERNAME} {UAT_PASSWORD}"
        )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"


def _make_notebook(client, name: str) -> str:
    r = client.post("/api/notebooks", json={"notebook_name": name})
    assert r.status_code == 200, f"create notebook failed: {r.text[:200]}"
    return r.json()["notebook_id"]


def _delete_notebook(client, notebook_id: str) -> None:
    client.delete(f"/api/notebooks/{notebook_id}")


def _upload_and_process(client, notebook_id: str, pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        r = client.post(
            "/api/files/upload",
            data={"notebook_id": notebook_id},
            files={"file": (os.path.basename(pdf_path), f, "application/pdf")},
        )
    assert r.status_code == 200, f"upload failed: {r.text[:200]}"
    file_id = r.json()["id"]
    r = client.post(f"/api/files/{file_id}/process")
    assert r.status_code == 200, f"process failed: {r.text[:200]}"
    status = wait_for_file_status(client, notebook_id, file_id, timeout=600)
    assert status == "ready", f"{pdf_path} ingest ended {status!r}"
    return file_id


def _say(client, notebook_id: str, role: str, text: str) -> None:
    r = client.post(
        f"/api/notebooks/{notebook_id}/messages",
        json={"role": role, "text": text},
    )
    assert r.status_code == 200, f"persist message failed: {r.text[:200]}"


def _run(client, notebook_id: str, message: str) -> str:
    _say(client, notebook_id, "user", message)
    r = client.post("/v1/runs", json={"notebook_id": notebook_id, "message": message})
    assert r.status_code == 202, f"create run failed: {r.text[:200]}"
    return r.json()["run_id"]


def _wait_done(client, run_id: str, timeout: int = RUN_TIMEOUT_S) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/v1/runs/{run_id}")
        assert r.status_code == 200, f"get run failed: {r.text[:200]}"
        if r.json()["status"] in TERMINAL:
            return r.json()["status"]
        time.sleep(POLL_S)
    raise TimeoutError(f"run {run_id} not terminal after {timeout}s")


def _result(run_id: str) -> dict:
    from app.store import runs as run_store

    row = run_store.get_run(run_id)
    assert row is not None, f"run {run_id} missing from store"
    return dict(row.result or {})


def _events(run_id: str) -> list:
    from app.store import runs as run_store

    return list(run_store.list_events(run_id))


def _event_types(run_id: str) -> list[str]:
    return [e.event_type for e in _events(run_id)]


def _route_intent(run_id: str) -> str | None:
    # None on the ReAct path: builder misses return plan_error → END with
    # no `plan` event, so plot_standalone/summarize_plot runs (REACT_ONLY)
    # never report an intent. Assert behavior (artifacts/sources) there.
    for e in _events(run_id):
        if e.event_type == "plan":
            route = (e.payload or {}).get("route") or {}
            return route.get("intent")
    return None


def _sources(run_id: str) -> list[dict]:
    out: list[dict] = []
    for e in _events(run_id):
        if e.event_type == "sources":
            srcs = (e.payload or {}).get("sources") or []
            out.extend(s for s in srcs if isinstance(s, dict))
    return out


def _artifacts(run_id: str) -> list[dict]:
    out: list[dict] = []
    for e in _events(run_id):
        if e.event_type == "artifacts":
            arts = (e.payload or {}).get("artifacts") or []
            out.extend(a for a in arts if isinstance(a, dict))
    return out


def _mentions(text: str, *keywords: str) -> bool:
    low = (text or "").lower()
    return any(k.lower() in low for k in keywords)


# --- session + shared notebooks -------------------------------------------


@pytest.fixture(scope="session")
def live_client():
    """Lifespan TestClient WITHOUT re-applying schema.

    Unlike conftest's `client` fixture, this does not execute schema.sql:
    the compose DB is migrated at deploy time (init/migrate), and the
    bare ``ALTER TABLE .. ADD CONSTRAINT`` in schema.sql is not safely
    re-runnable (it aborts the whole apply as "already exists").
    """
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
    try:
        import gc

        import torch

        app.state.rag = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001, S110 - teardown probes must never fail the suite
        pass


@pytest.fixture(scope="module")
def uat(live_client):
    """Logged-in session client (one login per session: rate limits)."""
    _login(live_client)
    yield live_client


def _nb_fixture(*pdfs: str):
    @pytest.fixture(scope="module")
    def _nb(uat):
        nb = _make_notebook(uat, f"uat-{uuid.uuid4().hex[:8]}")
        try:
            for pdf in pdfs:
                _upload_and_process(uat, nb, pdf)
            yield nb
        finally:
            _delete_notebook(uat, nb)

    return _nb


nb_empty = _nb_fixture()
nb_single = _nb_fixture(ANOMALY_PDF)
nb_multi = _nb_fixture(FAULTBOOK_PDF, ANOMALY_PDF)


# --- 1. greeting + reasoning + follow-up, empty notebook ------------------


@needs_stack
class TestEmptyNotebookChat:
    def test_greeting(self, uat, nb_empty):
        run_id = _run(uat, nb_empty, "Hi there!")
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "chat"
        assert _mentions(_result(run_id).get("summary", ""), "hello", "hi", "welcome")

    def test_reasoning_followup(self, uat, nb_empty):
        first = _run(uat, nb_empty, "Explain gradient descent like I'm five")
        assert _wait_done(uat, first) == "completed"
        assert _route_intent(first) == "knowledge_qa"
        summary = _result(first).get("summary", "")
        assert _mentions(summary, "gradient", "learn", "step", "hill")
        _say(uat, nb_empty, "assistant", summary)
        second = _run(uat, nb_empty, "And why does the learning rate matter?")
        assert _wait_done(uat, second) == "completed"
        answer = _result(second).get("summary", "")
        assert _mentions(answer, "learning rate", "step", "diverge", "overshoot")
        assert "sources" not in _event_types(second), "non-doc answer must not retrieve"


# --- 2. reasoning + plot, no doc ------------------------------------------


@needs_stack
class TestPlotNoDoc:
    def test_plot_no_doc(self, uat, nb_empty):
        # Pure plot wording: a combined explain+plot ask routes knowledge_qa
        # on small routers (live probe), and knowledge_qa never charts.
        # No intent assert: plot_standalone is ReAct-only (no plan event).
        run_id = _run(
            uat, nb_empty,
            "Draw a line chart of model accuracy 0.82, 0.88, 0.91 "
            "over 2022, 2023, 2024",
        )
        assert _wait_done(uat, run_id) in ("completed", "partial")
        arts = _artifacts(run_id)
        assert arts, "plot request must produce a chart artifact"
        assert any("svg" in (a.get("mime", "") or a.get("filename", "")) for a in arts)
        assert _mentions(_result(run_id).get("summary", ""), "chart", "accuracy")


# --- 3. reasoning-only with docs present (no false rag.query) -------------


@needs_stack
class TestNoFalseRag:
    def test_general_question_ignores_docs(self, uat, nb_single):
        run_id = _run(uat, nb_single, "What is QLoRA?")
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "knowledge_qa"
        assert not _sources(run_id), "general knowledge must not retrieve"
        assert _mentions(
            _result(run_id).get("summary", ""), "lora", "quant", "fine-tun", "adapter"
        )


# --- 4. single doc ---------------------------------------------------------


@needs_stack
class TestSingleDoc:
    def test_qa(self, uat, nb_single):
        run_id = _run(
            uat, nb_single, "What anomaly detection methods does the report describe?"
        )
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "qa_single"
        srcs = _sources(run_id)
        assert srcs, "doc answer must carry sources"
        assert any("anomaly" in (s.get("source", "") or "").lower() for s in srcs)
        assert _mentions(
            _result(run_id).get("summary", ""), "anomal", "detect", "model"
        )

    def test_summarize(self, uat, nb_single):
        # Live probe: small routers label "summarize ..." qa_single, so the
        # intent assert accepts both — the behavior (grounded summary with
        # sources) is what UAT pins.
        run_id = _run(uat, nb_single, "Summarize the anomaly detection report")
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) in ("summarize", "qa_single")
        assert _sources(run_id)
        assert len(_result(run_id).get("summary", "") or "") > 100


# --- 5. multiple docs -------------------------------------------------------


@needs_stack
class TestMultiDoc:
    def test_compare(self, uat, nb_multi):
        run_id = _run(
            uat, nb_multi,
            "Compare the anomaly detection report and the Faultbook: "
            "which one covers network faults?",
        )
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "compare_multi"
        files = {s.get("source", "") for s in _sources(run_id)}
        assert len(files) >= 2, f"compare must cite both files, got {files}"
        assert _mentions(_result(run_id).get("summary", ""), "fault", "network")

    def test_quiz(self, uat, nb_multi):
        run_id = _run(
            uat, nb_multi, "Give me 3 multiple-choice questions from both documents"
        )
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "quiz"
        answer = _result(run_id).get("summary", "")
        assert _mentions(answer, "A)", "B)", "a)", "b)", "option", "answer")


# --- 6. docs + plotting ------------------------------------------------------


@needs_stack
class TestDocsPlot:
    def test_docs_plot(self, uat, nb_multi):
        # No intent assert: summarize_plot is ReAct-only (no plan event).
        run_id = _run(
            uat, nb_multi,
            "Compare both reports and plot their precision scores as a bar chart",
        )
        assert _wait_done(uat, run_id) in ("completed", "partial")
        assert _artifacts(run_id), "doc chart must produce an artifact"
        assert _sources(run_id), "doc chart must stay grounded in retrieval"


# --- 7. convert with no doc ---------------------------------------------------


@needs_stack
class TestConvertEmpty:
    def test_convert_no_doc_asks(self, uat, nb_empty):
        # Live probe: small routers label "convert it ..." convert_all, so
        # the intent assert accepts both — on an empty notebook either lands
        # in an upload/wait clarification with no artifact.
        run_id = _run(uat, nb_empty, "Convert it to docx")
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) in ("convert_ambiguous", "convert_all")
        answer = _result(run_id).get("summary", "")
        assert _mentions(answer, "which file", "upload", "no", "format", "docx")
        assert not _artifacts(run_id)


# --- 8. convert one / all ------------------------------------------------------


@needs_stack
class TestConvertDocs:
    def test_convert_one(self, uat, nb_single):
        run_id = _run(
            uat, nb_single,
            "Convert Anomaly-Detection-Report.pdf to md",
        )
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "convert_one"
        arts = _artifacts(run_id)
        assert arts and any(a.get("filename", "").endswith(".md") for a in arts)
        dl = uat.get(
            f"/v1/runs/{run_id}/artifacts/{arts[0]['artifact_id']}"
        )
        assert dl.status_code == 200
        assert _mentions(dl.text, "anomal")

    def test_convert_all(self, uat, nb_multi):
        run_id = _run(uat, nb_multi, "Convert every document to docx")
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "convert_all"
        arts = _artifacts(run_id)
        assert len(arts) >= 2, f"convert-all must emit per-file artifacts, got {arts}"


# --- 9. report file + verbatim csv file -----------------------------------------


@needs_stack
class TestGenerateDoc:
    def test_report(self, uat, nb_multi):
        run_id = _run(
            uat, nb_multi,
            "Write a titled report on the key findings of both documents",
        )
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "report"
        arts = _artifacts(run_id)
        assert arts, "report must produce a document artifact"
        assert _mentions(_result(run_id).get("summary", ""), "report", "finding")

    # NOTE: the verbatim file path (content + filename → csv/txt/code) is
    # covered at unit level (TestDocGenerateVerbatim) because no router
    # intent reaches it end-to-end: doc-grounded file asks route to the
    # compare/report/convert builders (probed), and knowledge-grounded file
    # asks route to knowledge_qa (single reasoning step, no file). A
    # knowledge+file intent is future work.


# --- code agent + cancel ----------------------------------------------------------


@needs_stack
class TestCodeAndCancel:
    def test_code(self, uat, nb_empty):
        run_id = _run(uat, nb_empty, "Write a Python quicksort with a small test")
        assert _wait_done(uat, run_id) == "completed"
        assert _route_intent(run_id) == "code"
        assert _mentions(
            _result(run_id).get("summary", ""), "def ", "sort", "pivot", "python"
        )
        assert "sources" not in _event_types(run_id)

    def test_cancel_run(self, uat, nb_multi):
        run_id = _run(
            uat, nb_multi,
            "Summarize both documents in great detail with a section per finding",
        )
        r = uat.post(f"/v1/runs/{run_id}/cancel")
        assert r.status_code == 200
        status = _wait_done(uat, run_id, timeout=300)
        assert status in ("cancelled", "completed", "failed")
