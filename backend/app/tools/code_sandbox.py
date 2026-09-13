"""code.sandbox tool — fresh Docker container per execution.

Each snippet runs in its own ``docker run --rm`` container: no network,
memory and pids limits, wall-clock timeout, capped output. The host Docker
CLI is invoked directly (no new Python dependency). A missing daemon fails
honestly at execution — construction itself stays safe, so the core runs on
hosts without Docker.

Effect class: sandboxed.

RIP port: no plugin system — direct Tool subclass (ADR-017); plugin
init/health lifecycle removed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

from app.core.config import settings
from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.code_sandbox")


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


def run_python(code: str, *, timeout_ms: int | None = None) -> SandboxResult:
    """Run `code` in a fresh locked-down container; never raises.

    timeout_ms defaults to settings.sandbox_timeout_ms. Docker problems
    (missing CLI/daemon, timeouts) are reported IN the result, not raised —
    the tool converts them to ok=False responses.
    """
    timeout_s = (timeout_ms if timeout_ms is not None else settings.sandbox_timeout_ms) / 1000.0
    if shutil.which("docker") is None:
        return SandboxResult(stdout="", stderr="docker CLI not found", exit_code=127, timed_out=False)
    cmd = [
        "docker", "run", "--rm",
        # -i is LOAD-BEARING: without it the client never forwards our stdin
        # pipe, so `python -` sees EOF, runs an empty script, and exits 0
        # with no output (proven live: snippet silently dropped).
        "-i",
        "--network", "none",
        "--memory", settings.sandbox_memory,
        "--pids-limit", str(settings.sandbox_pids_limit),
        settings.sandbox_image,
        "python", "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        return SandboxResult(stdout=out, stderr=err or "timed out", exit_code=124, timed_out=True)
    except OSError as e:
        return SandboxResult(stdout="", stderr=f"docker failed: {e}", exit_code=127, timed_out=False)
    cap = settings.sandbox_output_max_bytes
    return SandboxResult(
        stdout=proc.stdout[-cap:],
        stderr=proc.stderr[-cap:],
        exit_code=proc.returncode,
        timed_out=False,
    )


class CodeSandboxTool(Tool):
    tool_id = "code.sandbox"
    name = "Code Sandbox"
    description = (
        "Execute a Python snippet in a fresh Docker container "
        "(no network, memory/pids limits, timeout) and return its output."
    )
    input_schema = {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    }
    output_schema = {
        "type": "object",
        "properties": {
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "exit_code": {"type": "integer"},
            "timed_out": {"type": "boolean"},
        },
    }
    effect_class = "sandboxed"  # type: ignore[assignment]
    cost_class = "medium"

    def execute(self, request: ToolRequest) -> ToolResponse:
        code = request.input.get("code")
        if not code:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'code' is required in input",
            )
        result = run_python(str(code))
        data = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }
        if result.timed_out:
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None,
                error=f"snippet timed out after {settings.sandbox_timeout_ms}ms",
            )
        if result.exit_code == 127:
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error=result.stderr or "docker unavailable",
            )
        if result.exit_code != 0:
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None,
                error=(result.stderr or f"exit code {result.exit_code}")[-2000:],
            )
        return ToolResponse(
            tool_id=self.tool_id, ok=True, output=result.stdout or "(no output)", data=data
        )
