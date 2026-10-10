"""Tool executor — deterministic dispatch, no approval gate.

RIP is local single-user: every registered tool executes directly when
dispatched (approval workflow removed per merge decision). The executor is
still the fail-honest boundary — tools never raise across it. Unexpected
exceptions become ok=False responses so the plan graph sees a normal
failure, exactly like the agent contract.
"""

from __future__ import annotations

import logging
import threading

from app.tools.base import (
    ToolRequest,
    ToolResponse,
)
from app.tools.registry import ToolRegistry

logger = logging.getLogger("tools.executor")


def execute_tool(
    registry: ToolRegistry,
    tool_id: str,
    tool_input: dict,
    *,
    step_id: str = "",
    trace_id: str = "",
    timeout_ms: int = 30000,
    cancel_event: threading.Event | None = None,
) -> ToolResponse:
    tool = registry.get(tool_id)
    from app.core.promptstore import is_tool_enabled

    if not is_tool_enabled(tool_id):
        # Honest failure (never raise): the plan graph maps ok=False to a
        # normal step FAILURE, and the validator rejects disabled tools
        # upfront — this guard covers races (disabled mid-run).
        return ToolResponse(
            tool_id=tool_id,
            ok=False,
            output=None,
            error=f"tool '{tool_id}' is disabled by admin (Admin → Tools to re-enable)",
        )
    request = ToolRequest(
        tool_id=tool_id,
        step_id=step_id,
        trace_id=trace_id,
        input=dict(tool_input),
        timeout_ms=timeout_ms,
        cancel_event=cancel_event,
    )
    try:
        response = tool.execute(request)
    except Exception as e:
        logger.exception("tool %s failed step=%s", tool_id, step_id)
        return ToolResponse(tool_id=tool_id, ok=False, output=None, error=str(e))
    if response.tool_id != tool_id:
        return ToolResponse(
            tool_id=tool_id,
            ok=False,
            output=None,
            error=f"tool identity mismatch: {response.tool_id!r} != {tool_id!r}",
        )
    return response
