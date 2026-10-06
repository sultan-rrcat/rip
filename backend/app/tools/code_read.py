"""code.read tool — read uploaded code/text file content from disk.

Fallback-scenario reader for L3 ReAct: the L2 `build_code` builder inlines
file content at plan time, but when the builder misses (validation failure,
unresolvable hint) the ReAct loop needs the same bytes on demand. This tool
is that on-demand path: given a `file_id` (preferred — literal snapshot id,
never invented) or a `file_name` (alias within the notebook), it returns the
file's text content wrapped in a Markdown code fence.

`notebook_id` is injected by the orchestrator from `Run.notebook_id`, never
LLM-generated (same contract as rag.query / notebook.inspect).

Security mirrors `builders._read_code_file`:
- `file_id` must be a DB UUID (no path separators reach open());
- `notebook_id` must match the safe pattern;
- ownership is verified against the `files` table;
- PDF/DOCX sources are refused (use doc.convert / rag.query for those).

Effect class: read-only.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, ClassVar

from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.code_read")

#: Same caps as the L2 CODE builder — one prompt must fit the Ollama window.
_CODE_MAX_BYTES_PER_FILE = 32 * 1024

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_NOTEBOOK_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

#: Map file extensions to Markdown code-fence language identifiers.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "jsx",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".m": "objectivec",
    ".sh": "bash",
    ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".json": "json",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".txt": "text",
    ".md": "markdown",
}

#: Binary document sources live on a different path (doc.convert / rag.query).
_BINARY_EXTS = frozenset({".pdf", ".docx"})


def _disk_path(notebook_id: str, file_id: str, ext: str) -> str:
    from app.core.config import settings as _settings

    return os.path.join(str(_settings.upload_dir), str(notebook_id), f"{file_id}{ext}")


def _read_disk(notebook_id: str, file_id: str, file_name: str) -> str:
    ext = os.path.splitext(file_name or "")[1].lower()
    if not ext:
        raise ValueError(f"file '{file_name}' has no extension — cannot read as text")
    if ext in _BINARY_EXTS:
        raise ValueError(
            f"file '{file_name}' is a binary document — use doc.convert "
            "to render it or rag.query to search it, not code.read"
        )
    path = _disk_path(notebook_id, file_id, ext)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(_CODE_MAX_BYTES_PER_FILE + 1)
    except OSError as e:
        raise ValueError(f"source file missing on disk for '{file_name}'") from e


def _resolve_by_id(
    notebook_id: str, file_id: str
) -> tuple[str, str]:
    """Validate ownership; return (file_id, file_name). Raises ValueError."""
    from app.core.db import pg_connection

    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT notebook_id, file_name, file_status FROM files WHERE file_id = %s",
            (str(file_id),),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown file_id: {file_id}")
    owner_nb, file_name, _status = str(row[0]), row[1] or "file", row[2]
    if owner_nb != str(notebook_id):
        raise ValueError(f"file {file_id} does not belong to this notebook")
    return str(file_id), str(file_name)


def _resolve_by_name(
    notebook_id: str, file_name: str
) -> tuple[str, str]:
    """Resolve `file_name` within the notebook; return (file_id, file_name).

    Exact match first, then case-insensitive substring (mirrors the L2
    builder's hint matching). Ambiguous substrings fail honest with the
    candidate list so the model can retry with a literal file_id.
    Raises ValueError.
    """
    from app.core.db import pg_connection

    hint = (file_name or "").strip()
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT file_id, file_name FROM files "
            "WHERE notebook_id = %s ORDER BY created_at ASC",
            (str(notebook_id),),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError("no files in this notebook")
    for r in rows:
        if str(r[1] or "") == hint:
            return str(r[0]), str(r[1])
    lowered = hint.lower()
    matched = [
        (str(r[0]), str(r[1])) for r in rows if lowered in str(r[1] or "").lower()
    ]
    if len(matched) == 1:
        return matched[0]
    if not matched:
        names = ", ".join(str(r[1]) for r in rows[:8])
        raise ValueError(
            f"no file matching '{hint}' in this notebook (files: {names})"
        )
    names = ", ".join(n for _, n in matched[:8])
    raise ValueError(
        f"multiple files match '{hint}' ({names}) — "
        "retry with a literal file_id from notebook.inspect"
    )


class CodeReadTool(Tool):
    tool_id = "code.read"
    name = "Code Read"
    description = (
        "Read an uploaded code/text file's content from disk and return it "
        "as fenced text for the coding agent. Input: file_id (preferred — "
        "a literal id from notebook.inspect, never invented) or file_name "
        "(alias within the notebook; substring matches when unambiguous). "
        "NEVER use {{...}} placeholders for file_id. "
        "Use FIRST for code tasks (write/test/explain/review/debug code) "
        "when the file content is not already in an observation — then pass "
        "the returned content into the coding agent's message."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string"},
            "file_id": {"type": "string"},
            "file_name": {"type": "string"},
        },
        "required": ["notebook_id"],
    }
    output_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "file_name": {"type": "string"},
            "truncated": {"type": "boolean"},
        },
    }
    effect_class = "read-only"  # type: ignore[assignment]
    cost_class = "low"

    def execute(self, request: ToolRequest) -> ToolResponse:
        notebook_id = request.input.get("notebook_id")
        if not notebook_id or not isinstance(notebook_id, str):
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'notebook_id' is required in input (injected by the orchestrator, never the LLM)",
            )
        if not _SAFE_NOTEBOOK_RE.match(notebook_id):
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="invalid notebook_id",
            )
        raw_fid = request.input.get("file_id")
        raw_name = request.input.get("file_name")
        file_id = str(raw_fid).strip() if isinstance(raw_fid, str) else ""
        file_name_hint = str(raw_name).strip() if isinstance(raw_name, str) else ""
        if file_id and ("{{" in file_id or "}}" in file_id):
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'file_id' must be a literal snapshot id, never a {{id}} placeholder",
            )
        try:
            if file_id:
                if not _UUID_RE.match(file_id):
                    return ToolResponse(
                        tool_id=self.tool_id,
                        ok=False,
                        output=None,
                        error=f"unknown file_id: {file_id}",
                    )
                fid, name = _resolve_by_id(notebook_id, file_id)
            elif file_name_hint:
                fid, name = _resolve_by_name(notebook_id, file_name_hint)
            else:
                return ToolResponse(
                    tool_id=self.tool_id,
                    ok=False,
                    output=None,
                    error="'file_id' (or 'file_name') is required in input — "
                    "get one from notebook.inspect first",
                )
            content = _read_disk(notebook_id, fid, name)
        except ValueError as e:
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error=str(e)
            )
        except Exception as e:
            logger.exception("code.read lookup failed")
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error=f"lookup failed: {e}"
            )
        truncated = len(content) > _CODE_MAX_BYTES_PER_FILE
        if truncated:
            content = content[:_CODE_MAX_BYTES_PER_FILE]
        lang = _EXT_TO_LANG.get(os.path.splitext(name)[1].lower(), "text")
        marker = "\n…[truncated — file continues beyond what is shown]" if truncated else ""
        output = f"File {name}:\n```{lang}\n{content}\n```{marker}"
        data: dict[str, Any] = {
            "file_id": fid,
            "file_name": name,
            "truncated": truncated,
            "notebook_id": str(notebook_id),
        }
        return ToolResponse(tool_id=self.tool_id, ok=True, output=output, data=data)
