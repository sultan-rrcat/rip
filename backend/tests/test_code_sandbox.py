"""code.sandbox tool (ADR-049): staging, honest failures, kill-switch.

DB-free: the container runner (`_run_container`) and the file listing are
monkeypatched; only the validation/timeout/manifest/validator paths run
for real. Docker-daemon suites stay on the operator's fresh stack.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from app.core.config import settings
from app.tools import code_sandbox as sbx
from app.tools.base import ToolRequest

NB = "nb-sbx-1"


def _tool_request(**kwargs) -> ToolRequest:
    base: dict = {"notebook_id": NB, "task": "run the tests"}
    base.update(kwargs)
    return ToolRequest(tool_id="code.sandbox", step_id="1", trace_id="t",
                       input=base)


def _stage_disk(tmp_path, files: dict[str, str]) -> str:
    root = tmp_path / "uploads" / NB
    root.mkdir(parents=True)
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    return str(tmp_path / "uploads")


def _listing(names: list[str]):
    return [(f"fid-{i}", name) for i, name in enumerate(names)]


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _json_text(text: str) -> str:
    return json.dumps({"part": {"type": "text", "text": text}})


def test_success_stages_and_reports_changes(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "print('hi')\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: _listing(["hello.py"]))
    # Map staged file_id name -> disk name for the copy step.
    monkeypatch.setattr(sbx, "_match_scope",
                        lambda scope, files: ([("fid-0", "hello.py")], []))
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))

    seen: dict = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        seen["timeout"] = timeout_s
        vol = cmd[cmd.index("-v") + 1]
        src = vol.rsplit(":", 1)[0]  # rsplit: Windows drive letters hold a colon
        cfg_path = os.path.join(src, "opencode.json")
        assert os.path.isfile(cfg_path)
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        assert "ollama-local" in cfg["provider"]
        with open(os.path.join(src, "result.txt"), "w", encoding="utf-8") as f:
            f.write("done\n")
        return _FakeCompleted(0, _json_text("ALL GREEN") + "\n")

    monkeypatch.setattr(sbx, "_run_container", fake_run)
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is True
    assert "ALL GREEN" in (resp.output or "")
    assert "result.txt" in resp.data["changed_files"]
    assert resp.data["model"] == "granite"
    assert "--rm" in seen["cmd"]
    assert "rip-sandbox" in " ".join(seen["cmd"]) or settings.sandbox_image in seen["cmd"]


def test_empty_task_and_bad_notebook_fail_honest():
    tool = sbx.CodeSandboxTool()
    assert tool.execute(_tool_request(task="   ")).ok is False
    assert tool.execute(_tool_request(notebook_id="nope/evil")).ok is False
    assert tool.execute(_tool_request(notebook_id="")).ok is False


def test_scope_miss_fails_with_file_list(monkeypatch):
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: _listing(["a.py"]))
    resp = sbx.CodeSandboxTool().execute(
        _tool_request(file_names=["missing.py"]))
    assert resp.ok is False
    assert "a.py" in (resp.error or "")


def test_timeout_is_honest(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))

    def fake_run(cmd, timeout_s):
        raise subprocess.TimeoutExpired(cmd, timeout_s)

    monkeypatch.setattr(sbx, "_run_container", fake_run)
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is False
    assert "timed out" in (resp.error or "")


def test_no_docker_fails_honest(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is False
    assert "docker" in (resp.error or "")


def test_nonzero_exit_reports_stderr(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))
    monkeypatch.setattr(
        sbx, "_run_container",
        lambda cmd, timeout_s: _FakeCompleted(1, "", "boom: auth failed"))
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is False
    assert "boom" in (resp.error or "")


def test_pick_model_unreachable_falls_back():
    model, reachable = sbx._pick_model("http://127.0.0.1:9", "pref-model")
    assert (model, reachable) == ("pref-model", False)


def test_sandbox_base_url_rewrites_loopback(monkeypatch):
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    assert sbx._sandbox_base_url() == "http://host.docker.internal:11434"
    monkeypatch.setattr(settings, "ollama_base_url", "http://10.10.30.77:21434")
    assert sbx._sandbox_base_url() == "http://10.10.30.77:21434"


def test_registered_manifest_and_validator():
    from app.agents.reasoning import ReasoningAgent
    from app.agents.registry import AgentRegistry
    from app.orchestration.plan import Plan, PlanStep
    from app.orchestration.validator import PlanValidationError, PlanValidator
    from app.providers.base import ModelProvider
    from app.tools.registry import get_default_tool_registry

    class _P(ModelProvider):
        def generate(self, *a, **k): raise AssertionError
        def generate_stream(self, *a, **k): raise AssertionError
        def generate_structured(self, *a, **k): raise AssertionError
        def embed(self, *a, **k): raise AssertionError
        def list_available_models(self): return []
        def served_model(self, m): return m

    tools = get_default_tool_registry()
    manifest = {m["tool_id"]: m for m in tools.manifest()}
    assert manifest["code.sandbox"]["effect_class"] == "sandboxed"
    assert manifest["code.sandbox"]["enabled"] is True

    agents = AgentRegistry()
    agents.register(ReasoningAgent(_P()))
    plan = Plan(plan_id="p", goal="g", steps=[
        PlanStep(step_id="1", tool_id="code.sandbox",
                 input={"notebook_id": "n", "task": "run tests"},
                 expected_output_type="text"),
    ])
    PlanValidator(agents, tools).validate(plan)
    from app.core import promptstore as ps

    ps._cache_set_tool("code.sandbox", {"enabled": False,
                                        "description_override": None})
    try:
        with pytest.raises(PlanValidationError, match="disabled tool"):
            PlanValidator(agents, tools).validate(plan)
    finally:
        ps._cache_set_tool("code.sandbox", {"enabled": True,
                                            "description_override": None})
