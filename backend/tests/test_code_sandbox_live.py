"""Live daemon test for code.sandbox (ADR-050 full workflow).

Runs the REAL container lifecycle — create → cp-in → start -a (opencode
vs host Ollama) → cp-out → rm -f — with only the DB file listing stubbed.
Skips cleanly when the daemon, the `rip-sandbox` image, or Ollama is
missing, so plain `pytest` stays green on machines without a stack:

    docker compose build sandbox   # one-time image build (SETUP.md)
    pytest backend/tests/test_code_sandbox_live.py -s
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from app.core.config import settings
from app.tools import code_sandbox as sbx
from app.tools.base import ToolRequest

NB = "live-nb-1"
FID = "fid-live-1"


def _ollama_ok(base_url: str) -> bool:
    try:
        with sbx.urlopen(f"{base_url.rstrip('/')}/api/tags",
                         timeout=8) as res:
            json.loads(res.read().decode("utf-8") or "{}")
        return True
    except Exception:  # noqa: BLE001 - probe is boolean
        return False


def _require_stack(monkeypatch, tmp_path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    image = subprocess.run(
        ["docker", "image", "inspect", settings.sandbox_image],
        capture_output=True, check=False)
    if image.returncode != 0:
        pytest.skip(f"image {settings.sandbox_image} not built "
                    "(docker compose build sandbox)")
    if _ollama_ok(settings.ollama_base_url):
        pass
    elif _ollama_ok("http://localhost:11434"):
        # Windows host-local: the gateway name need not route back to the
        # host even with Ollama on localhost (probe uses the host view).
        monkeypatch.setattr(settings, "ollama_base_url",
                            "http://localhost:11434")
    else:
        pytest.skip("Ollama unreachable at "
                    f"{settings.ollama_base_url} and localhost:11434")
    upload_dir = tmp_path / "uploads" / NB
    upload_dir.mkdir(parents=True)
    (upload_dir / f"{FID}.js").write_text('console.log("hi");\n',
                                          encoding="utf-8")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(sbx, "_notebook_code_files",
                        lambda nb: [(FID, "hello.js")])


def test_live_write_file_roundtrip(monkeypatch, tmp_path) -> None:
    _require_stack(monkeypatch, tmp_path)
    before = subprocess.run(["docker", "ps", "-aq"], capture_output=True,
                            text=True, check=False).stdout.split()
    req = ToolRequest(
        tool_id="code.sandbox", step_id="1", trace_id="live",
        input={"notebook_id": NB,
               "task": ("Use the write tool to create result.txt containing "
                        "exactly this text: done. "
                        "Report the file you created.")},
        timeout_ms=180000)
    start = time.monotonic()
    resp = sbx.CodeSandboxTool().execute(req)
    elapsed = time.monotonic() - start
    after = subprocess.run(["docker", "ps", "-aq"], capture_output=True,
                           text=True, check=False).stdout.split()
    leaked = sorted(set(after) - set(before))
    assert not leaked, f"container leak: {leaked}"
    assert resp.ok, f"sandbox failed after {elapsed:.0f}s: {resp.error}"
    assert "result.txt" in (resp.data.get("changed_files") or [])
    assert "ses_" not in (resp.output or ""), "raw event JSON leaked"
    raw = {e["filename"]: base64.b64decode(e["b64"])
           for e in resp.data.get("sandbox_files", [])}
    assert raw.get("result.txt"), "changed bytes missing from payload"
