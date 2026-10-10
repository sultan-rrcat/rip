"""code.sandbox tool — agentic code execution in an ephemeral local container.

ADR-049: stages the notebook's code files plus a generated `opencode.json`
(pinning a pulled Ollama model, no credentials), runs opencode inside an
ephemeral container against host Ollama, and returns stdout plus the
changed-file list as the observation. ADR-050 (L2 execute DAG): changed
file bytes additionally ride `ToolResponse.data["sandbox_files"]` so the
run worker persists them as review artifacts — originals are never
overwritten. Host-stored opencode credentials are never used — the box
talks only to Ollama over the host gateway, so the offline thesis holds.

Staging uses `docker create` + `docker cp` (never a `-v` host path): the
backend itself may run in a container behind the same docker socket, where
a container-local temp path would resolve to nothing on the host.

Honest-failure contract (never raises across the executor boundary…
this module returns ok=False itself, matching the other tools):
- missing/invalid input, no docker CLI, Ollama unreachable, non-zero exit,
  timeout, staging failures, or empty output all become ok=False with a
  named reason.
- Ambiguous file scopes are skipped with a note (listed in the output);
  a scope matching nothing fails honest with the notebook file list.

Effect class: sandboxed. Test seam: `_run_container` is module-level so
tests monkeypatch it without a daemon.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import ClassVar
from urllib.request import urlopen

from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.code_sandbox")

_SAFE_NOTEBOOK_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

#: `docker create` output carries the 64-hex container id.
_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}")

#: Stdout budget for the observation (mirrors the retrieval scratchpad scale).
_SANDBOX_OUTPUT_MAX_CHARS = 12000

#: Stderr tail kept on non-zero exit.
_SANDBOX_STDERR_TAIL = 2000

#: Per-call deadline for the fast staging ops (create/cp/rm carry no model).
_SANDBOX_STAGE_TIMEOUT_S = 90.0

#: Model names that are embeddings, never code executors.
_EMBED_MARKERS = ("bge", "embed")

#: Review-artifact caps for changed files (ADR-050, observation-only: the
#: chat shows the report, the bytes travel via run artifacts).
_SBX_ARTIFACT_MAX_FILES = 8
_SBX_ARTIFACT_MAX_BYTES = 100 * 1024


def _run_container(cmd: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    """Run one sandbox container (monkeypatched in tests).

    UTF-8 with replacement: opencode/model output is UTF-8 JSON, and the
    platform locale (cp1252 on Windows hosts) would otherwise crash the
    reader thread on smart quotes/ellipsis.
    """
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout_s, check=False)


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _sandbox_base_url() -> str:
    """Ollama base URL as seen FROM INSIDE the sandbox container.

    A host-local backend points at localhost (the container's own
    loopback — wrong); rewrite loopback to the Desktop gateway. LAN
    addresses stay verbatim (routable from the bridge).
    """
    from app.core.config import settings

    base = (settings.ollama_base_url or "").strip().rstrip("/")
    for loopback in ("http://localhost", "https://localhost",
                     "http://127.0.0.1", "https://127.0.0.1", "http://::1"):
        if base.startswith(loopback):
            rest = base[len(loopback):]
            scheme = "https://" if "https" in loopback else "http://"
            return f"{scheme}host.docker.internal{rest}"
    return base


def _pick_model(base_url: str, preferred: str) -> tuple[str, bool]:
    """Resolve the sandbox model: preferred when pulled, else first
    non-embedding pulled model. Returns (model, ollama_reachable).

    `base_url` is the caller's view of Ollama (probed from this process);
    the box-view URL for `opencode.json` is built separately by
    `_sandbox_base_url`, since a host-local loopback must be rewritten to
    the container gateway — probing the rewritten URL from the host would
    fail wherever the gateway name doesn't route back (Windows host-local
    runs), even with Ollama listening on localhost.
    """
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=8) as res:
            data = json.loads(res.read().decode("utf-8") or "{}")
        names = [m.get("name") for m in data.get("models", []) if m.get("name")]
    except Exception as e:  # noqa: BLE001 - unreachable Ollama is honest failure
        logger.warning("sandbox: ollama unreachable at %s (%s)", base_url, e)
        return preferred, False
    if preferred in names:
        return preferred, True
    for name in names:
        lowered = name.lower()
        if not any(marker in lowered for marker in _EMBED_MARKERS):
            return name, True
    return preferred, True


def _opencode_config(model: str, base_url: str) -> str:
    return json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {"edit": "allow", "bash": "allow"},
        "provider": {
            "ollama-local": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama local (sandbox)",
                "options": {"baseURL": f"{base_url.rstrip('/')}/v1"},
                "models": {model: {"name": model}},
            }
        },
    })


def _extract_text(stdout: str) -> str:
    """Join `text` parts of `opencode run --format json` event lines.

    Three tiers: model prose (`part.type == "text"`) wins; without prose,
    failed tool calls summarize as one line each (a box that only errored
    still reports honestly instead of dumping raw event JSON); otherwise
    only non-JSON lines are kept (older CLI human output). Pure event
    noise with no errors yields "" so the caller fails honest instead of
    surfacing session ids.
    """
    chunks: list[str] = []
    errors: list[str] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
        elif part.get("type") == "tool":
            state = part.get("state")
            if (isinstance(state, dict) and state.get("status") == "error"
                    and isinstance(state.get("error"), str)):
                tool = part.get("tool") or "tool"
                errors.append(f"tool {tool} failed: {state['error'][:500]}")
    if chunks:
        return "\n".join(chunks).strip()
    if errors:
        return "\n".join(errors).strip()
    rest = [ln for ln in (stdout or "").splitlines()
            if not ln.strip().startswith("{")]
    return "\n".join(rest).strip()


def _notebook_code_files(notebook_id: str) -> list[tuple[str, str]]:
    """All code files of the notebook as (file_id, file_name)."""
    from app.core.config import settings
    from app.core.db import pg_connection

    code_exts = set(settings.code_extensions or [])
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT file_id, file_name FROM files "
            "WHERE notebook_id = %s ORDER BY created_at ASC",
            (str(notebook_id),),
        )
        rows = cur.fetchall()
    out: list[tuple[str, str]] = []
    for file_id, file_name in rows:
        name = str(file_name or "")
        ext = os.path.splitext(name)[1].lower()
        if ext and ext in code_exts:
            out.append((str(file_id), name))
    return out


def _parse_container_id(output: str) -> str:
    """Best-effort container id out of `docker create` stdout."""
    match = _CONTAINER_ID_RE.search(output or "")
    if match:
        return match.group(0)
    text = (output or "").strip()
    return text.splitlines()[-1].strip() if text else ""


def _match_scope(scope: list[str], files: list[tuple[str, str]]
                 ) -> tuple[list[tuple[str, str]], list[str]]:
    """Resolve scope names: exact first, then substring (all matches).
    Returns (resolved, notes). Ambiguous hints resolve to every substring
    match — the sandbox sees files, not ids, so over-inclusion is safe."""
    resolved: list[tuple[str, str]] = []
    notes: list[str] = []
    seen: set[str] = set()
    for hint in scope:
        hint = (hint or "").strip()
        if not hint:
            continue
        exact = [f for f in files if f[1] == hint]
        if exact:
            for f in exact:
                if f[0] not in seen:
                    seen.add(f[0])
                    resolved.append(f)
            continue
        lowered = hint.lower()
        matched = [f for f in files if lowered in f[1].lower()]
        if matched:
            notes.append(f"scope '{hint}' matched {len(matched)} file(s)")
            for f in matched:
                if f[0] not in seen:
                    seen.add(f[0])
                    resolved.append(f)
        else:
            names = ", ".join(n for _, n in files[:8]) or "none"
            notes.append(f"scope '{hint}' matched nothing (files: {names})")
    return resolved, notes


class CodeSandboxTool(Tool):
    tool_id = "code.sandbox"
    name = "Code Sandbox"
    description = (
        "Execute a coding task inside an ephemeral local Docker container "
        "running the opencode CLI against host Ollama (ADR-049). The "
        "notebook's code files are staged into the box; stdout plus the "
        "changed-file list come back as the observation. Use for run/test/ "
        "execute asks (run the code, run tests, fix failures by running) — "
        "NOT for read/explain/review (code.read + coding agent, no container). "
        "Input: notebook_id (injected, never invented), task (full ask), "
        "optional file_names scope, optional model override. NEVER invent "
        "execution results: report only what the box returned."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string"},
            "task": {"type": "string"},
            "file_names": {"type": "array", "items": {"type": "string"}},
            "model": {"type": "string"},
            "timeout_ms": {"type": "integer"},
        },
        "required": ["notebook_id", "task"],
    }
    output_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "changed_files": {"type": "array"},
            "staged_files": {"type": "array"},
            "model": {"type": "string"},
            "sandbox_files": {"type": "array"},
        },
    }
    effect_class = "sandboxed"  # type: ignore[assignment]
    cost_class = "high"

    def execute(self, request: ToolRequest) -> ToolResponse:
        from app.core.config import settings

        notebook_id = request.input.get("notebook_id")
        if not notebook_id or not isinstance(notebook_id, str):
            return self._fail(
                "'notebook_id' is required in input (injected by the orchestrator, never the LLM)")
        if not _SAFE_NOTEBOOK_RE.match(notebook_id):
            return self._fail("invalid notebook_id")
        task = request.input.get("task")
        if not task or not isinstance(task, str) or not task.strip():
            return self._fail("'task' must be a non-empty string")
        if not _docker_available():
            return self._fail(
                "docker CLI not available — build the sandbox image and "
                "mount the docker socket (see SETUP), then retry")

        scope = request.input.get("file_names") or []
        scope = [s for s in scope if isinstance(s, str) and s.strip()]
        try:
            files = _notebook_code_files(notebook_id)
        except Exception as e:  # noqa: BLE001 - DB failure is honest, not fatal
            return self._fail(f"could not list notebook files: {e}")
        notes: list[str] = []
        if scope:
            staged, notes = _match_scope(scope, files)
            if not staged:
                names = ", ".join(n for _, n in files) or "none"
                return self._fail(
                    f"scope matched no code files (notebook files: {names})")
        else:
            staged = list(files)
        if not staged:
            notes.append("no code files staged — running greenfield (task only)")

        base_url = _sandbox_base_url()
        # Probe Ollama from THIS process (host view): the box-view URL
        # above may rewrite loopback to the container gateway, which the
        # host itself often cannot route back to (Windows host-local).
        # Same server either way, so the pulled-model listing is identical.
        probe_url = (settings.ollama_base_url or "").strip().rstrip("/")
        model_override = request.input.get("model")
        preferred = (str(model_override).strip() if isinstance(model_override, str)
                     and model_override.strip() else settings.ollama_default_model)
        model, reachable = _pick_model(probe_url, preferred)
        if not reachable:
            return self._fail(
                f"ollama unreachable at {settings.ollama_base_url} — "
                "start it, then retry")
        timeout_ms = request.input.get("timeout_ms")
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            timeout_ms = settings.sandbox_timeout_ms
        timeout_ms = min(timeout_ms, 480000)
        # Respect the step wall-clock carried on the request: the tool must
        # return before the plan graph reports the step timed out.
        step_budget = getattr(request, "timeout_ms", 0)
        if isinstance(step_budget, int) and step_budget > 0:
            timeout_ms = min(timeout_ms, step_budget)

        stage = tempfile.mkdtemp(prefix="rip-sbx-")
        fetch = tempfile.mkdtemp(prefix="rip-sbx-out-")
        originals: dict[str, bytes] = {}
        cid = ""
        try:
            staged_names: list[str] = []
            for file_id, name in staged:
                ext = os.path.splitext(name)[1].lower()
                src = os.path.join(str(settings.upload_dir), notebook_id, f"{file_id}{ext}")
                try:
                    with open(src, "rb") as f:
                        blob = f.read()
                except OSError:
                    notes.append(f"staged-missing: '{name}' not on disk, skipped")
                    continue
                safe_name = os.path.basename(name) or f"file-{file_id[:8]}"
                with open(os.path.join(stage, safe_name), "wb") as f:
                    f.write(blob)
                originals[safe_name] = blob
                staged_names.append(safe_name)
            with open(os.path.join(stage, "opencode.json"), "w", encoding="utf-8") as f:
                f.write(_opencode_config(model, base_url))

            logger.info("sandbox start notebook=%s files=%d model=%s timeout_ms=%d",
                        notebook_id, len(staged_names), model, timeout_ms)
            try:
                created = _run_container(
                    ["docker", "create",
                     "--cpus", str(settings.sandbox_cpus),
                     "--memory", str(settings.sandbox_memory),
                     "-w", "/work",
                     settings.sandbox_image,
                     "opencode", "run",
                     "-m", f"ollama-local/{model}",
                     "--format", "json",
                     task.strip()],
                    _SANDBOX_STAGE_TIMEOUT_S,
                )
            except FileNotFoundError:
                return self._fail("docker CLI not available at execution time")
            except Exception as e:  # noqa: BLE001 - spawn failure is honest
                return self._fail(f"sandbox spawn failed: {e}")
            if created.returncode != 0:
                tail = (created.stderr or "")[-_SANDBOX_STDERR_TAIL:]
                return self._fail(
                    f"sandbox create failed: {tail or 'no stderr'}")
            cid = _parse_container_id(created.stdout or "")
            if not cid:
                return self._fail("sandbox create returned no container id")
            try:
                cp_in = _run_container(
                    ["docker", "cp", f"{stage}/.", f"{cid}:/work"],
                    _SANDBOX_STAGE_TIMEOUT_S,
                )
            except FileNotFoundError:
                return self._fail("docker CLI not available at execution time")
            except Exception as e:  # noqa: BLE001 - staging failure is honest
                return self._fail(f"sandbox staging failed: {e}")
            if cp_in.returncode != 0:
                tail = (cp_in.stderr or "")[-_SANDBOX_STDERR_TAIL:]
                return self._fail(
                    f"sandbox staging failed: {tail or 'no stderr'}")
            try:
                proc = _run_container(
                    ["docker", "start", "-a", cid], timeout_ms / 1000.0)
            except subprocess.TimeoutExpired:
                return self._fail(
                    f"sandbox run timed out after {timeout_ms}ms — "
                    "narrow the task or raise sandbox_timeout_ms")
            except FileNotFoundError:
                return self._fail("docker CLI not available at execution time")
            except Exception as e:  # noqa: BLE001 - spawn failure is honest
                return self._fail(f"sandbox spawn failed: {e}")
            if proc.returncode != 0:
                tail = (proc.stderr or "")[-_SANDBOX_STDERR_TAIL:]
                return self._fail(
                    f"sandbox exited {proc.returncode}: {tail or 'no stderr'}")

            output = _extract_text(proc.stdout or "")[:_SANDBOX_OUTPUT_MAX_CHARS]
            changed: list[str] = []
            sandbox_files: list[dict] = []
            try:
                fetched = _run_container(
                    ["docker", "cp", f"{cid}:/work/.", fetch],
                    _SANDBOX_STAGE_TIMEOUT_S,
                )
                if fetched.returncode != 0:
                    notes.append("change-scan incomplete: docker cp back failed")
                else:
                    for root, _, filenames in os.walk(fetch):
                        for filename in filenames:
                            if filename == "opencode.json":
                                continue
                            full = os.path.join(root, filename)
                            rel = os.path.relpath(full, fetch).replace(os.sep, "/")
                            with open(full, "rb") as f:
                                blob = f.read()
                            if originals.get(rel) != blob:
                                changed.append(rel)
                                if len(sandbox_files) >= _SBX_ARTIFACT_MAX_FILES:
                                    notes.append(
                                        f"artifact cap: '{rel}' observed but not "
                                        "kept for review")
                                    continue
                                if len(blob) > _SBX_ARTIFACT_MAX_BYTES:
                                    notes.append(
                                        f"artifact cap: '{rel}' exceeds "
                                        f"{_SBX_ARTIFACT_MAX_BYTES // 1024}KB, "
                                        "not kept for review")
                                    continue
                                sandbox_files.append({
                                    "filename": rel,
                                    "b64": base64.b64encode(blob).decode("ascii"),
                                })
            except FileNotFoundError:
                notes.append("change-scan incomplete: docker CLI went away")
            except OSError as e:
                notes.append(f"change-scan incomplete: {e}")
            if not output.strip() and not changed:
                return self._fail(
                    "sandbox returned no text and changed no files")
            head = "\n".join(f"- {n}" for n in notes) + ("\n" if notes else "")
            return ToolResponse(
                tool_id=self.tool_id, ok=True,
                output=f"{head}sandbox model={model} staged={len(staged_names)} "
                       f"changed={len(changed)}\n{output}".strip(),
                data={"changed_files": sorted(changed),
                      "staged_files": sorted(staged_names),
                      "model": model,
                      "sandbox_files": sandbox_files},
            )
        finally:
            if cid:
                try:
                    _run_container(["docker", "rm", "-f", cid], 30.0)
                except Exception as e:  # noqa: BLE001 - teardown is best-effort
                    logger.debug("sandbox teardown rm failed: %s", e)
            shutil.rmtree(stage, ignore_errors=True)
            shutil.rmtree(fetch, ignore_errors=True)

    def _fail(self, error: str) -> ToolResponse:
        logger.warning("code.sandbox honest failure: %s", error)
        return ToolResponse(tool_id=self.tool_id, ok=False, output=None, error=error)
