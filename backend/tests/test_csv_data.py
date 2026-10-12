"""CSV data path (ADR-051): disk-only data files + pandas sandbox DAG.

DB-free: config/corpus/router-dispatch/plan-shape are pure; the sandbox
container runner is monkeypatched; the upload dir is a tmp_path.
"""

from __future__ import annotations

import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.agents.coding import CodingAgent
from app.agents.reasoning import ReasoningAgent
from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.orchestration import builders as B
from app.orchestration.builders import build
from app.orchestration.corpus import (
    _ready_data,
    _snapshot_files,
    get_corpus_state,
)
from app.orchestration.intents import Intent
from app.orchestration.router import (
    _CODE_FILENAME_RE,
    _corpus_hint_for_router,
    _extract_file_hint,
)
from app.orchestration.validator import PlanValidator
from app.providers.base import ModelProvider
from app.tools import code_sandbox as sbx
from app.tools.base import ToolRequest
from app.tools.registry import get_default_tool_registry

FID = "11111111-1111-1111-1111-111111111111"
FID2 = "22222222-2222-2222-2222-222222222222"
NB = "nb-csv-1"
CID = "b" * 64
CSV_SNAPSHOT = f"1 file(s): sales.csv [ready:data] id={FID}"
MIXED_SNAPSHOT = (
    f"2 file(s): app.py [ready:code] id={FID}; sales.csv [ready:data] id={FID2}"
)


class _P(ModelProvider):
    def generate(self, *a, **k): raise AssertionError
    def generate_stream(self, *a, **k): raise AssertionError
    def generate_structured(self, *a, **k): raise AssertionError
    def embed(self, *a, **k): raise AssertionError
    def list_available_models(self): return []
    def served_model(self, m): return m


def _validator() -> PlanValidator:
    agents = AgentRegistry()
    agents.register(ReasoningAgent(_P()))
    agents.register(CodingAgent(_P()))
    return PlanValidator(agents, get_default_tool_registry())


def _route(intent: Intent, file_hint: str = ""):
    from app.orchestration.router import RouterResult

    return RouterResult(intent=intent, confidence=1.0,
                        routed_by="llm", file_hint=file_hint)


# --- config ---

def test_csv_allowed_but_not_code():
    assert ".csv" in (settings.allowed_extensions or [])
    assert ".csv" in (settings.data_extensions or [])
    assert ".csv" not in (settings.code_extensions or [])


# --- corpus ---

def test_snapshot_parses_ready_data():
    rows = _snapshot_files(CSV_SNAPSHOT)
    assert rows == [("sales.csv", "ready:data", FID)]
    assert _ready_data(CSV_SNAPSHOT) == [("sales.csv", FID)]


def test_data_only_has_no_retrievable_corpus():
    assert get_corpus_state(CSV_SNAPSHOT) == "empty"
    assert get_corpus_state(MIXED_SNAPSHOT) == "empty"


# --- router ---

def test_csv_filename_recognized_as_code_hint():
    m = _CODE_FILENAME_RE.search("analyze sales.csv please")
    assert m and m.group(1) == "sales.csv"
    assert _extract_file_hint("analyze sales.csv", Intent.CODE) == "sales.csv"


def test_data_only_hint_names_csv_as_code_task():
    hint = _corpus_hint_for_router(CSV_SNAPSHOT)
    assert hint is not None and "DATA-ONLY" in hint
    assert "sales.csv" in hint
    assert "CODE" in hint


def test_ready_docs_keep_data_suffix():
    hint = _corpus_hint_for_router(
        f"2 file(s): r.pdf [ready] id={FID}; sales.csv [ready:data] id={FID2}"
    )
    assert hint is not None and "READY" in hint
    assert "sales.csv" in hint


# --- builders ---

def test_named_csv_builds_sandbox_dag():
    plan = build("analyze sales.csv", _route(Intent.CODE, "sales.csv"), CSV_SNAPSHOT)
    assert plan is not None and len(plan.steps) == 2
    first, second = plan.steps
    assert first.tool_id == "code.sandbox"
    assert first.input["data_files"] == ["sales.csv"]
    assert "file_names" not in first.input
    assert second.agent_id == "coding"
    assert second.depends_on == ["1"]
    assert "{{1}}" in str(second.input)
    _validator().validate(plan)


def test_data_only_without_hint_builds_sandbox_dag():
    plan = build("summarize the data", _route(Intent.CODE, ""), CSV_SNAPSHOT)
    assert plan is not None and len(plan.steps) == 2
    assert plan.steps[0].tool_id == "code.sandbox"
    assert plan.steps[0].input["data_files"] == ["sales.csv"]
    _validator().validate(plan)


def test_greenfield_code_without_data_signal_stays_code_path():
    plan = build("write a quicksort in python", _route(Intent.CODE, ""), CSV_SNAPSHOT)
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].agent_id == "coding"
    assert not any(s.tool_id for s in plan.steps)
    _validator().validate(plan)


def test_code_hint_with_code_present_stays_code_path():
    plan = build("run the tests", _route(Intent.CODE, "app.py"), MIXED_SNAPSHOT)
    assert plan is not None and len(plan.steps) == 2
    assert plan.steps[0].tool_id == "code.sandbox"
    assert plan.steps[0].input.get("file_names") == ["app.py"]
    assert "data_files" not in plan.steps[0].input
    _validator().validate(plan)


def test_named_but_missing_csv_is_clarification():
    plan = build("analyze nope.csv", _route(Intent.CODE, "nope.csv"), CSV_SNAPSHOT)
    assert plan is not None and len(plan.steps) == 1
    assert (plan.steps[0].expected_output_type or "").lower() == "clarification"
    _validator().validate(plan)


def test_vague_summarize_over_data_falls_to_csv_dag():
    plan = build("summarize the data", _route(Intent.SUMMARIZE, ""), CSV_SNAPSHOT)
    assert plan is not None and len(plan.steps) == 2
    assert plan.steps[0].tool_id == "code.sandbox"
    _validator().validate(plan)


def test_csv_head_inlined_for_column_names(tmp_path, monkeypatch):
    root = tmp_path / "uploads" / NB
    root.mkdir(parents=True)
    body = "month,revenue\njan,10\nfeb,20\n"
    (root / f"{FID}.csv").write_bytes(body.encode())
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    plan = B.build_csv_analyze("analyze sales.csv", "sales.csv", CSV_SNAPSHOT, NB)
    assert plan is not None and len(plan.steps) == 2
    assert "month,revenue" in str(plan.steps[1].input)
    _validator().validate(plan)


def test_csv_report_carries_error_ref():
    plan = build("analyze sales.csv", _route(Intent.CODE, "sales.csv"), CSV_SNAPSHOT)
    assert plan is not None and len(plan.steps) == 2
    message = str(plan.steps[1].input.get("message", ""))
    assert "{{1}}" in message
    assert "{{error:1}}" in message
    _validator().validate(plan)


# --- sandbox tool ---

class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _json_text(text: str) -> str:
    return json.dumps({"part": {"type": "text", "text": text}})


def _make_daemon(monkeypatch, *, out_files=None, stage_seen=None):
    out_files = dict(out_files or {})

    def fake_run(cmd, timeout_s):
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "create":
            return _FakeCompleted(0, CID + "\n", "")
        if sub == "cp":
            src, dst = cmd[2], cmd[3]
            if dst.endswith(":/work"):
                if stage_seen is not None:
                    stage = src.removesuffix("/.")
                    stage_seen.append(sorted(os.listdir(stage)))
                return _FakeCompleted(0, "", "")
            for rel, content in out_files.items():
                path = os.path.join(dst, rel)
                os.makedirs(os.path.dirname(path) or dst, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content.encode("utf-8"))
            return _FakeCompleted(0, "", "")
        if sub == "start":
            return _FakeCompleted(0, _json_text("mean revenue 15.0") + "\n")
        if sub == "rm":
            return _FakeCompleted(0, "", "")
        raise AssertionError(f"unexpected docker call: {cmd}")

    monkeypatch.setattr(sbx, "_run_container", fake_run)


def _csv_request(**kwargs):
    base: dict = {"notebook_id": NB, "task": "analyze sales.csv",
                  "data_files": ["sales.csv"]}
    base.update(kwargs)
    return ToolRequest(tool_id="code.sandbox", step_id="1", trace_id="t",
                       input=base, timeout_ms=30000)


def test_data_file_staged_read_only_and_not_changed(monkeypatch, tmp_path):
    root = tmp_path / "uploads" / NB
    root.mkdir(parents=True)
    (root / f"{FID}.csv").write_bytes(b"month,revenue\njan,10\n")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(sbx, "_notebook_code_files", lambda nb: [])
    monkeypatch.setattr(sbx, "_notebook_data_files",
                        lambda nb: [(FID, "sales.csv")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))
    stage_seen: list = []
    _make_daemon(monkeypatch, out_files={"analysis.txt": "mean 10\n"},
                 stage_seen=stage_seen)
    resp = sbx.CodeSandboxTool().execute(_csv_request())
    assert resp.ok is True
    # CSV reached the box...
    assert stage_seen and "sales.csv" in stage_seen[0]
    assert "opencode.json" in stage_seen[0]
    # ...but the unchanged CSV is not a review artifact.
    assert resp.data["staged_data_files"] == ["sales.csv"]
    assert resp.data["changed_files"] == ["analysis.txt"]
    assert "data staged: sales.csv" in (resp.output or "")


def test_data_scope_miss_fails_honest(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(sbx, "_notebook_code_files", lambda nb: [])
    monkeypatch.setattr(sbx, "_notebook_data_files",
                        lambda nb: [(FID, "sales.csv")])
    resp = sbx.CodeSandboxTool().execute(_csv_request(data_files=["nope.csv"]))
    assert resp.ok is False
    assert "sales.csv" in (resp.error or "")


def test_no_data_scope_stages_no_csv(monkeypatch, tmp_path):
    root = tmp_path / "uploads" / NB
    root.mkdir(parents=True)
    (root / f"{FID}.csv").write_bytes(b"a,b\n1,2\n")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(sbx, "_notebook_code_files", lambda nb: [(FID, "a.py")])
    monkeypatch.setattr(sbx, "_notebook_data_files",
                        lambda nb: [(FID, "sales.csv")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))
    stage_seen: list = []
    _make_daemon(monkeypatch, stage_seen=stage_seen)
    req = ToolRequest(tool_id="code.sandbox", step_id="1", trace_id="t",
                      input={"notebook_id": NB, "task": "run it"},
                      timeout_ms=30000)
    # Code-only run: the unrelated CSV must not ride along.
    monkeypatch.setattr(sbx, "_match_scope",
                        lambda scope, files: ([("fid-0", "a.py")], []))
    resp = sbx.CodeSandboxTool().execute(req)
    assert resp.ok is True
    assert "sales.csv" not in (stage_seen[0] if stage_seen else [])
    assert resp.data.get("staged_data_files") == []
