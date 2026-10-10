"""code.sandbox tool (ADR-049/050): staging, honest failures, kill-switch.

DB-free: the container runner (`_run_container`) and the file listing are
monkeypatched; only the validation/timeout/manifest/validator paths run
for real. Docker-daemon suites stay on the operator's fresh stack.

The tool talks to the daemon as create → cp-in → start -a → cp-out → rm:
`docker cp` (never a `-v` host path) so staging works when the backend
itself runs in a container behind the same docker socket.
"""

from __future__ import annotations

import base64
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
CID = "a" * 64


def _tool_request(timeout_ms: int = 30000, **kwargs) -> ToolRequest:
    base: dict = {"notebook_id": NB, "task": "run the tests"}
    base.update(kwargs)
    return ToolRequest(tool_id="code.sandbox", step_id="1", trace_id="t",
                       input=base, timeout_ms=timeout_ms)


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


def _make_daemon(monkeypatch, *, out_files=None, start=None):
    """Fake `docker` CLI dispatching on subcommand; returns seen calls.

    `out_files`: {relpath: text} materialized into the cp-out target dir.
    `start`: _FakeCompleted (or exception instance) for `docker start`.
    """
    seen: list = []
    out_files = dict(out_files or {})
    start_resp = start if start is not None else _FakeCompleted(
        0, _json_text("ALL GREEN") + "\n")

    def fake_run(cmd, timeout_s):
        seen.append({"cmd": cmd, "timeout": timeout_s})
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "create":
            assert "--cpus" in cmd and "--memory" in cmd
            assert "-w" in cmd and "/work" in cmd
            assert "opencode" in cmd and "run" in cmd
            return _FakeCompleted(0, CID + "\n", "")
        if sub == "cp":
            src, dst = cmd[2], cmd[3]
            if dst.endswith(":/work"):
                stage = src.removesuffix("/.")
                cfg_path = os.path.join(stage, "opencode.json")
                assert os.path.isfile(cfg_path), stage
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                assert "ollama-local" in cfg["provider"]
                return _FakeCompleted(0, "", "")
            for rel, content in out_files.items():
                path = os.path.join(dst, rel)
                os.makedirs(os.path.dirname(path) or dst, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content.encode("utf-8"))
            return _FakeCompleted(0, "", "")
        if sub == "start":
            assert cmd[2] == "-a" and cmd[3] == CID
            if isinstance(start_resp, BaseException):
                raise start_resp
            return start_resp
        if sub == "rm":
            assert CID in cmd
            return _FakeCompleted(0, "", "")
        raise AssertionError(f"unexpected docker call: {cmd}")

    monkeypatch.setattr(sbx, "_run_container", fake_run)
    return seen


def test_success_stages_and_reports_changes(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "print('hi')\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: _listing(["hello.py"]))
    # Map staged file_id name -> disk name for the copy step.
    monkeypatch.setattr(sbx, "_match_scope",
                        lambda scope, files: ([("fid-0", "hello.py")], []))
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))

    seen = _make_daemon(monkeypatch, out_files={
        "hello.py": "print('hi')\nprint('patched')\n",
        "result.txt": "done\n",
    })
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is True
    assert "ALL GREEN" in (resp.output or "")
    assert sorted(resp.data["changed_files"]) == ["hello.py", "result.txt"]
    assert resp.data["model"] == "granite"
    assert resp.data["staged_files"] == ["hello.py"]
    # Review bytes ride the payload for the run worker's artifacts.
    by_name = {e["filename"]: e for e in resp.data["sandbox_files"]}
    assert set(by_name) == {"hello.py", "result.txt"}
    assert base64.b64decode(by_name["result.txt"]["b64"]) == b"done\n"
    # Lifecycle is create → cp → start → cp → rm (no `docker run --rm`).
    subs = [c["cmd"][1] for c in seen]
    assert subs == ["create", "cp", "start", "cp", "rm"]
    assert settings.sandbox_image in seen[0]["cmd"]


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


def test_timeout_is_honest_and_tears_down(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))

    def fake_run(cmd, timeout_s):
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "create":
            return _FakeCompleted(0, CID + "\n", "")
        if sub == "cp":
            return _FakeCompleted(0, "", "")
        if sub == "start":
            raise subprocess.TimeoutExpired(cmd, timeout_s)
        if sub == "rm":
            return _FakeCompleted(0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(sbx, "_run_container", fake_run)
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is False
    assert "timed out" in (resp.error or "")


def test_timeout_clamped_to_step_budget(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))
    seen = _make_daemon(monkeypatch)
    # Step wall-clock (request.timeout_ms) beats the 240s tool default.
    resp = sbx.CodeSandboxTool().execute(_tool_request(timeout_ms=5000))
    assert resp.ok is True
    start = next(c for c in seen if c["cmd"][1] == "start")
    assert start["timeout"] == 5.0


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
    _make_daemon(monkeypatch, start=_FakeCompleted(1, "", "boom: auth failed"))
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is False
    assert "boom" in (resp.error or "")


def test_create_failure_is_honest(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))

    def fake_run(cmd, timeout_s):
        if cmd[1] == "create":
            return _FakeCompleted(125, "", "image not found")
        if cmd[1] == "rm":
            return _FakeCompleted(0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(sbx, "_run_container", fake_run)
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is False
    assert "create failed" in (resp.error or "")


def test_cp_in_failure_is_honest(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))

    def fake_run(cmd, timeout_s):
        if cmd[1] == "create":
            return _FakeCompleted(0, CID + "\n", "")
        if cmd[1] == "cp":
            return _FakeCompleted(1, "", "no such container path")
        if cmd[1] == "rm":
            return _FakeCompleted(0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(sbx, "_run_container", fake_run)
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is False
    assert "staging failed" in (resp.error or "")


def test_oversize_change_observed_but_not_kept(monkeypatch, tmp_path):
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    monkeypatch.setattr(sbx, "_pick_model", lambda base, pref: ("granite", True))
    big = "y\n" * (sbx._SBX_ARTIFACT_MAX_BYTES // 2 + 10)
    _make_daemon(monkeypatch, out_files={"big.py": big})
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is True
    assert "big.py" in resp.data["changed_files"]
    assert all(e["filename"] != "big.py" for e in resp.data["sandbox_files"])
    assert "exceeds" in (resp.output or "")


def test_pick_model_unreachable_falls_back():
    model, reachable = sbx._pick_model("http://127.0.0.1:9", "pref-model")
    assert (model, reachable) == ("pref-model", False)


def test_extract_text_prefers_prose_over_tool_errors():
    prose = _json_text("done") + "\n" + json.dumps(
        {"part": {"type": "tool", "tool": "edit",
                  "state": {"status": "error", "error": "bad args"}}})
    assert sbx._extract_text(prose) == "done"


def test_extract_text_summarizes_tool_errors():
    err = json.dumps(
        {"part": {"type": "tool", "tool": "edit",
                  "state": {"status": "error", "error": "bad args"}}})
    out = sbx._extract_text(err + "\n")
    assert out == "tool edit failed: bad args"
    assert "ses_" not in out and "prt_" not in out


def test_extract_text_drops_pure_event_noise():
    noise = json.dumps({"type": "step_start", "part": {"type": "step-start"}})
    assert sbx._extract_text(noise + "\n") == ""


def test_model_probe_uses_host_view_not_gateway(monkeypatch, tmp_path):
    # Host-local backend with a loopback OLLAMA_BASE_URL: the pulled-model
    # probe must hit the host-view URL (reachable from this process), while
    # the staged opencode.json keeps the gateway URL for the box.
    upload_dir = _stage_disk(tmp_path, {"fid-0.py": "x\n"})
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [("fid-0", "a.py")])
    probed: list = []

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"models": [{"name": "granite"}]}).encode()

    def fake_urlopen(url, timeout=None):
        probed.append(url)
        return _Resp()

    monkeypatch.setattr(sbx, "urlopen", fake_urlopen)
    seen = _make_daemon(monkeypatch)
    resp = sbx.CodeSandboxTool().execute(_tool_request())
    assert resp.ok is True
    assert probed and probed[0].startswith("http://localhost:11434"), probed
    create = next(c for c in seen if c["cmd"][1] == "create")
    assert "ollama-local/granite" in create["cmd"]
    assert resp.data["model"] == "granite"


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
