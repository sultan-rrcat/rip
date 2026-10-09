"""L3 ReAct fallback — extracted from `Orchestrator.run()` for testability.

When no L2 deterministic builder applies (unknown intent, non-deterministic
shape, routing miss), the orchestrator delegates to this module. It runs
the ReAct loop, aggregates the result, and maps it to an
`OrchestrationResult`. On failure it returns the actionable error message
(the ReAct aggregation summary) so the orchestrator can raise
`OrchestrationError` with context.

The module owns:
  - the `react` span (trace-only sibling of `plan` under `run`)
  - the ReAct → Aggregator → OrchestrationResult mapping
  - error message selection (react_summary or plan_error)

The orchestrator owns:
  - checking `plan_error` exists
  - checking cancellation
  - raising `OrchestrationError`
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.agents.registry import AgentRegistry
from app.orchestration.aggregator import Aggregator
from app.providers.base import ModelProvider
from app.tools.registry import ToolRegistry

# OrchestrationResult is defined in orchestrator.py; imported lazily
# inside run() to avoid a circular import (orchestrator imports ReactFallback).

logger = logging.getLogger("orchestration")


class ReactFallback:
    """Runs the ReAct loop when no L2 builder applies.

    Composition-time dependencies (provider, registries, aggregator) are
    bound in the constructor; per-request values ride on `run()`.
    """

    def __init__(
        self,
        provider: ModelProvider,
        agents: AgentRegistry,
        tools: ToolRegistry,
        aggregator: Aggregator,
    ):
        self._provider = provider
        self._agents = agents
        self._tools = tools
        self._aggregator = aggregator

    def run(
        self,
        request_text: str,
        *,
        notebook_id: str,
        trace_id: str,
        plan_error: str,
        context: str | None = None,
        notebook_context: str | None = None,
        cancel_event: threading.Event | None = None,
        on_event: Callable[[dict], None] | None = None,
        parent_span_ctx: dict[str, str] | None = None,
    ) -> tuple[OrchestrationResult | None, str | None]:
        """Run the ReAct fallback.

        Returns `(result, error_message)`:
        - `(OrchestrationResult, None)` on success — the run recovered.
        - `(None, error_message)` on failure — `error_message` is the
          actionable summary (ReAct aggregation summary or the original
          plan_error).
        """
        from app.observability.langfuse import (
            get_trace_context as _get_tc,
        )
        from app.observability.langfuse import (
            manual_span as _manual_span,
        )
        from app.observability.langfuse import (
            truncate as _truncate,
        )
        from app.orchestration.orchestrator import OrchestrationResult
        from app.orchestration.react_engine import run_react

        react_summary: str | None = None
        try:
            # Trace-only sibling: the `react` span parents explicitly
            # under `run` (via the worker-thread context captured here),
            # so Langfuse reads run → react → react:iter-N → step:rN.
            # Disabled path is a no-op; failures still fall through to
            # the original honest error below.
            with _manual_span(
                "react",
                as_type="span",
                input={
                    "request": _truncate(request_text, 2000),
                    "plan_error": _truncate(plan_error, 500),
                },
                trace_context=parent_span_ctx or _get_tc(),
            ) as react_obs:
                react = run_react(
                    request_text,
                    self._provider,
                    self._agents,
                    self._tools,
                    trace_id=trace_id,
                    notebook_id=notebook_id,
                    context=context,
                    notebook_context=notebook_context,
                    cancel_event=cancel_event,
                    on_event=on_event,
                    parent_span_ctx=_get_tc(),
                )
                aggregation = self._aggregator.aggregate(react.plan, react.result)
                react_obs.update(output={
                    "status": aggregation.status,
                    "steps": len(react.plan.steps),
                    "iterations": len(react.result.step_results),
                    "summary": _truncate(aggregation.summary, 2000),
                })
                if aggregation.status != "failed":
                    logger.info(
                        "react fallback recovered plan=%s steps=%d trace=%s",
                        react.plan.plan_id, len(react.plan.steps), trace_id,
                    )
                    return OrchestrationResult(
                        trace_id=trace_id,
                        plan_id=react.plan.plan_id,
                        goal=react.plan.goal,
                        step_results=list(react.result.step_results),
                        summary=aggregation.summary,
                        status=aggregation.status,
                        plan_incomplete=aggregation.plan_incomplete,
                        conflicts=aggregation.conflicts,
                        needs_clarification=aggregation.needs_clarification,
                        shown=list(aggregation.shown),
                        hidden=list(aggregation.hidden),
                        visibility=dict(aggregation.visibility),
                    ), None
                react_summary = aggregation.summary
        except Exception as e:  # noqa: BLE001 - react miss → original honest error
            logger.warning("react fallback failed: %s", e)
        return None, (react_summary or plan_error)