"""notebook.inspect tool — list files in a notebook (dynamic discovery).

Read-only inventory for the planner: returns `{file_id, file_name,
file_size, file_status}` rows for the run's notebook so the planner can
resolve "which document" without guessing. `notebook_id` is injected by
the orchestrator from `Run.notebook_id`, never LLM-generated (same
contract as rag.query).

Effect class: read-only.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.notebook_inspect")


def _format_files(files: list[dict]) -> str:
    if not files:
        return "(no files in this notebook)"
    parts = [
        f"{f.get('file_name', '?')} ({f.get('file_status', '?')})"
        for f in files
    ]
    return f"{len(files)} file(s): " + ", ".join(parts)


class NotebookInspectTool(Tool):
    tool_id = "notebook.inspect"
    name = "Notebook Inspect"
    description = (
        "List files in this notebook with id, name, size and status "
        "(ready/processing/error). Call first when the request refers to "
        "'this document', 'convert', 'which file', or when the notebook "
        "snapshot may be stale."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string"},
        },
        "required": ["notebook_id"],
    }
    output_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "files": {"type": "array"},
        },
    }
    effect_class = "read-only"  # type: ignore[assignment]
    cost_class = "low"

    def execute(self, request: ToolRequest) -> ToolResponse:
        notebook_id = request.input.get("notebook_id")
        if not notebook_id:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'notebook_id' is required in input (injected by the orchestrator, never the LLM)",
            )
        try:
            from app.core.db import pg_connection

            with pg_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT file_id, file_name, file_size, file_status
                    FROM files
                    WHERE notebook_id = %s
                    ORDER BY created_at ASC
                    """,
                    (str(notebook_id),),
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.exception("notebook.inspect query failed")
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error=f"inspect failed: {e}",
            )
        files: list[dict[str, Any]] = [
            {
                "file_id": str(r[0]),
                "file_name": r[1],
                "file_size": r[2],
                "file_status": r[3],
            }
            for r in rows
        ]
        return ToolResponse(
            tool_id=self.tool_id,
            ok=True,
            output=_format_files(files),
            data={"files": files, "notebook_id": str(notebook_id)},
        )
