"""Inner plan graph — the per-request LangGraph DAG.

Builds and runs a StateGraph from a VALIDATED Plan at runtime:

    START ──► step with no depends_on ──► ... ──► sinks ──► END
    (edges = depends_on; LangGraph runs independent siblings of a
     super-step in parallel)

Per-step node bodies are FACTORIES: each closure captures its PlanStep plus
run-level constants (trace id, notebook id, memory context, fallback
message) — things that cannot change while the graph runs. The graph STATE
carries only what actually flows between nodes: `step_results`, keyed by
step_id behind a merge reducer so parallel siblings write disjoint keys.

Semantics:
  - {{step_id}} placeholder resolution BEFORE the step executes
  - fallback message injection when a step has no `message`
  - short-term-memory `context` injection into every step
  - `notebook_id` injection into every TOOL step input (Q30): the run's
    notebook id comes from Run.notebook_id, never the LLM — the closure
    overwrites any planner-emitted value
  - status-driven retry loop (agents never raise; LangGraph RetryPolicy is
    exception-driven and therefore does NOT apply to our contract)
  - per-step wall-clock timeout (reports FAILURE; cannot preempt a hung
    worker — pre-existing thread limitation)
  - no approval gate (local single-user: tools execute directly)

RIP port: approvals, Langfuse spans, and correlation helpers removed;
step lifecycle is reported through the opaque `on_event` callback as
plain dicts (the run worker maps these onto SSE in Phase 4).
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.base import DelegationRequest, StepStatus
from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.results import ExecutionResult, StepResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.plan_graph")

# Matches {{step_id}} placeholders inside a step's input values.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_-]+)\s*\}\}")

# Athena default kept as a code constant (no new Settings key): attempts
# beyond the first only help flaky steps; honest failure follows.
_DEFAULT_MAX_RETRIES = 2


def _trunc(text: str | None, limit: int = 2000) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "…"


def is_cancelled(cancel_event: threading.Event | None) -> bool:
    """Cooperative cancellation check.

    Threads cannot preempt each other, so cancellation is polled: at the top
    of every step node (between steps) and before every attempt inside the
    retry loop. A cancelled run short-circuits the remaining steps to a
    FAILURE("run cancelled") result.
    """
    return cancel_event is not None and cancel_event.is_set()


def merge_dicts(left: dict, right: dict) -> dict:
    # Module-level ON PURPOSE: LangGraph resolves Annotated reducers via
    # typing.get_type_hints against the module namespace.
    return {**left, **right}


class PlanGraphState(TypedDict):
    """State of the inner plan graph.

    Only data that CHANGES while the graph runs lives here. Sibling steps in
    a super-step each write their OWN key (disjoint writes), so the merge
    reducer is order-free. Downstream nodes read upstream results from this
    dict — the O(1) by-step_id lookup.
    """

    step_results: Annotated[dict[str, StepResult], merge_dicts]


def _outputs_from(results: dict[str, StepResult]) -> dict[str, str]:
    """Successful outputs by step_id — the placeholder-resolution source."""
    return {
        sid: r.output
        for sid, r in results.items()
        if r.status is StepStatus.SUCCESS and r.output is not None
    }


def _try_numeric(s: str) -> int | float | str:
    """Try to parse a string as a number; return the original string on failure.

    This enables tool steps (like plot.chart) to receive numeric values from
    upstream step outputs (e.g. a counting agent returning "29").
    """
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _resolve_value(value: object, outputs: dict[str, str]) -> object:
    if isinstance(value, str):
        # Check if the entire string is a single placeholder (e.g. "{{1}}").
        # If so, resolve and try numeric conversion — enables tool steps to
        # receive numeric values from upstream step outputs.
        m = _PLACEHOLDER.fullmatch(value)
        if m:
            resolved = outputs.get(m.group(1), m.group(0))
            return _try_numeric(resolved)
        # Embedded placeholder (e.g. "Result is {{1}}") — string substitution only.
        return _PLACEHOLDER.sub(
            lambda m: outputs.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: _resolve_value(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, outputs) for v in value]
    return value


def _resolve_input(raw: dict, outputs: dict[str, str]) -> dict:
    return {k: _resolve_value(v, outputs) for k, v in raw.items()}


def _invoke_with_wall_clock(
    step: PlanStep,
    node_ctx: contextvars.Context,
    body: Callable[[], StepResult],
    timeout_ms: int,
    cancel_event: threading.Event | None = None,
) -> StepResult:
    """Run one step's full body under a wall-clock deadline.

    A timeout REPORTS FAILURE but cannot preempt the hung worker
    (pre-existing thread limitation). shutdown(wait=False) matters: a
    context-managed executor would block the node until the hung body
    finished, silently turning the reported timeout into a longer stall.
    Cooperative cancel: checked before submit and again on result.
    """
    if is_cancelled(cancel_event):
        return _cancelled_result(step)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future: Future = executor.submit(node_ctx.run, body)
        return future.result(timeout=timeout_ms / 1000)
    except FuturesTimeoutError:
        return StepResult(
            step_id=step.step_id,
            agent_id=step.executor_id,
            status=StepStatus.FAILURE,
            error="step timed out",
        )
    except Exception as e:  # noqa: BLE001 - fail-honest boundary
        return StepResult(
            step_id=step.step_id,
            agent_id=step.executor_id,
            status=StepStatus.FAILURE,
            error=str(e),
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _cancelled_result(step: PlanStep) -> StepResult:
    return StepResult(
        step_id=step.step_id,
        agent_id=step.executor_id,
        status=StepStatus.FAILURE,
        error="run cancelled",
    )


def _run_step_body(
    step: PlanStep,
    trace_id: str,
    resolved_input: dict,
    registry: AgentRegistry,
    max_retries: int,
    timeout_ms: int,
    cancel_event: threading.Event | None = None,
    on_delta: Callable[[str], None] | None = None,
    tool_registry: ToolRegistry | None = None,
) -> StepResult:
    """Delegation + status-driven retry loop.

    Agent steps delegate to the AgentRegistry; tool steps dispatch through
    the tool executor (no approval gate — local single-user). All paths
    share the retry loop and the StepResult shape (StepResult.agent_id
    carries the executor identity — agent_id or tool_id).
    """
    import json as _json

    from app.tools.executor import execute_tool as _execute_tool

    is_tool = bool(step.tool_id)
    executor_id = step.tool_id or step.agent_id
    agent = None if is_tool else registry.get(step.agent_id)
    agent_request = (
        None
        if is_tool
        else DelegationRequest(
            step_id=step.step_id,
            trace_id=trace_id,
            input=resolved_input,
            timeout_ms=timeout_ms,
            on_delta=on_delta,
        )
    )

    response = None
    tool_data: dict = {}
    for attempt in range(max_retries + 1):
        if is_cancelled(cancel_event):
            return _cancelled_result(step)
        if is_tool:
            assert step.tool_id is not None  # narrowed by is_tool
            tools = tool_registry or ToolRegistry()
            tool_resp = _execute_tool(
                tools,
                step.tool_id,
                resolved_input,
                step_id=step.step_id,
                trace_id=trace_id,
                timeout_ms=timeout_ms,
            )
            status = StepStatus.SUCCESS if tool_resp.ok else StepStatus.FAILURE
            output = tool_resp.output
            if output is None and tool_resp.data:
                output = _json.dumps(tool_resp.data)[:4000]
            # Keep the raw tool payload for SSE sources/artifacts downstream.
            # Only the final attempt's data is kept.
            tool_data = dict(tool_resp.data) if tool_resp.data else {}
            from app.agents.base import DelegationResponse as _DelegationResponse

            response = _DelegationResponse(
                step_id=step.step_id,
                status=status,
                output=output,
                error=tool_resp.error,
            )
        else:
            assert agent is not None and agent_request is not None
            response = agent.execute(agent_request)  # never raises; failure status on error
        if response.status is StepStatus.SUCCESS:
            break  # success -> no more retries

        logger.warning(
            "step %s attempt=%d status=%s",
            step.step_id,
            attempt + 1,
            response.status.value,
        )

    if response is None:  # safeguard
        return StepResult(
            step_id=step.step_id,
            agent_id=executor_id,
            status=StepStatus.FAILURE,
            error="No response returned from step execution",
        )

    logger.info(
        "step %s executor=%s status=%s trace=%s",
        step.step_id,
        executor_id,
        response.status.value,
        trace_id,
    )
    return StepResult(
        step_id=step.step_id,
        agent_id=executor_id,
        status=response.status,
        output=response.output,
        error=response.error,
        needs_clarification=response.needs_clarification,
        data=tool_data if is_tool and response.status is StepStatus.SUCCESS else {},
    )


def _make_step_node(
    step: PlanStep,
    *,
    registry: AgentRegistry,
    tool_registry: ToolRegistry | None = None,
    trace_id: str,
    notebook_id: str | None,
    context: str | None,
    fallback_message: str | None,
    max_retries: int,
    timeout_ms: int,
    node_ctx: contextvars.Context,
    on_event: Callable[[dict], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Callable[[PlanGraphState], dict]:
    """Node factory: closure captures everything constant for this run.

    The returned node takes the graph state (upstream step_results so far),
    resolves its input against it, delegates, and returns its OWN key as a
    partial state update.
    """

    def node(state: PlanGraphState) -> dict:
        # Cancellation fires HERE, in the node body proper — before the
        # executor hop, so no thread is even spawned for a cancelled step.
        if is_cancelled(cancel_event):
            return {"step_results": {step.step_id: _cancelled_result(step)}}
        if on_event is not None:
            on_event(
                {
                    "type": "step_started",
                    "step_id": step.step_id,
                    "executor_id": step.executor_id,
                }
            )

        def _emit_delta(text: str) -> None:
            if on_event is not None:
                on_event(
                    {"type": "delta", "step_id": step.step_id, "content": text}
                )

        delta_sink = _emit_delta if on_event is not None else None

        def body() -> StepResult:
            # Resolve placeholders BEFORE executing so the step sees the
            # resolved, self-descriptive input.
            resolved_input = _resolve_input(
                step.input, _outputs_from(state["step_results"])
            )
            if "message" not in resolved_input and fallback_message:
                resolved_input["message"] = fallback_message
            if context and "context" not in resolved_input:
                resolved_input["context"] = context
            if step.expected_output_type and "expected_output_type" not in resolved_input:
                resolved_input["expected_output_type"] = step.expected_output_type
            if step.tool_id and notebook_id is not None:
                # Run-scoped truth wins over anything the planner emitted.
                resolved_input["notebook_id"] = notebook_id
            result = _run_step_body(
                step, trace_id, resolved_input, registry, max_retries, timeout_ms,
                cancel_event, delta_sink, tool_registry,
            )
            if on_event is not None:
                on_event(
                    {
                        "type": "step_completed",
                        "step_id": step.step_id,
                        "status": result.status.value,
                        "output": _trunc(result.output),
                    }
                )
            return result

        return {
            "step_results": {
                step.step_id: _invoke_with_wall_clock(
                    step, node_ctx, body, timeout_ms, cancel_event
                )
            }
        }

    return node


def build_plan_graph(
    plan: Plan,
    registry: AgentRegistry,
    *,
    tool_registry: ToolRegistry | None = None,
    trace_id: str,
    notebook_id: str | None = None,
    context: str | None,
    fallback_message: str | None,
    max_retries: int | None = None,
    timeout_ms: int | None = None,
    on_event: Callable[[dict], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[CompiledStateGraph, dict[str, contextvars.Context]]:
    """Compile a StateGraph from a validated Plan — per request, at runtime.

    Returns (compiled_graph, node_contexts): the caller streams the graph;
    node_contexts holds one contextvars snapshot per node.
    """
    retries = max_retries if max_retries is not None else _DEFAULT_MAX_RETRIES
    deadline = timeout_ms if timeout_ms is not None else settings.default_timeout_ms

    step_ids = {s.step_id for s in plan.steps}
    for s in plan.steps:
        unknown = set(s.depends_on) - step_ids
        if unknown:
            raise ValueError(
                f"step {s.step_id} depends on unknown step(s): {sorted(unknown)}"
            )

    # One contextvars copy per node, made HERE (calling thread, sequentially —
    # a Context cannot be entered concurrently, and copy_context() only copies
    # the CURRENT thread's context).
    base_ctx = contextvars.copy_context()
    node_ctxs = {s.step_id: base_ctx.run(contextvars.copy_context) for s in plan.steps}

    graph = StateGraph(PlanGraphState)
    for s in plan.steps:
        graph.add_node(
            s.step_id,
            _make_step_node(
                s,
                registry=registry,
                tool_registry=tool_registry,
                trace_id=trace_id,
                notebook_id=notebook_id,
                context=context,
                fallback_message=fallback_message,
                max_retries=retries,
                timeout_ms=deadline,
                node_ctx=node_ctxs[s.step_id],
                on_event=on_event,
                cancel_event=cancel_event,
            ),
            # Explicit input_schema: LangGraph's add_node is generically typed
            # over the node's input; mypy cannot solve that inference from a
            # plain Callable, though the runtime accepts this exact shape.
            input_schema=PlanGraphState,  # type: ignore[call-overload]
        )
    has_dependents = {dep for s in plan.steps for dep in s.depends_on}
    for s in plan.steps:
        if s.depends_on:
            for dep in s.depends_on:
                graph.add_edge(dep, s.step_id)
        else:
            graph.add_edge(START, s.step_id)
        if s.step_id not in has_dependents:
            graph.add_edge(s.step_id, END)
    return graph.compile(), node_ctxs


def run_plan_graph(
    plan: Plan,
    registry: AgentRegistry,
    *,
    tool_registry: ToolRegistry | None = None,
    trace_id: str,
    notebook_id: str | None = None,
    context: str | None = None,
    fallback_message: str | None = None,
    on_step_completed: Callable[[StepResult], None] | None = None,
    max_retries: int | None = None,
    timeout_ms: int | None = None,
    on_event: Callable[[dict], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> ExecutionResult:
    """Build + stream the plan graph, emitting events as super-steps complete.

    Streaming (not monolithic invoke) is load-bearing: callers receive step
    events AS they finish. Within a parallel super-step, LangGraph may batch
    sibling updates — cross-super-step ordering (upstream before downstream)
    is guaranteed, intra-wave order is not.
    """
    if plan.is_trivial():
        return ExecutionResult(trace_id=trace_id, step_results=[])

    started = time.monotonic()
    graph, _node_ctxs = build_plan_graph(
        plan,
        registry,
        tool_registry=tool_registry,
        trace_id=trace_id,
        notebook_id=notebook_id,
        context=context,
        fallback_message=fallback_message,
        max_retries=max_retries,
        timeout_ms=timeout_ms,
        on_event=on_event,
        cancel_event=cancel_event,
    )

    results: dict[str, StepResult] = {}
    for chunk in graph.stream({"step_results": {}}, stream_mode="updates"):
        for node_update in chunk.values():
            for step_id, step_result in node_update["step_results"].items():
                results[step_id] = step_result
                if on_step_completed is not None:
                    on_step_completed(step_result)

    logger.info(
        "executed plan=%s steps=%d trace=%s in %dms",
        plan.plan_id,
        len(plan.steps),
        trace_id,
        int((time.monotonic() - started) * 1000),
    )
    return ExecutionResult(
        trace_id=trace_id,
        step_results=[results[s.step_id] for s in plan.steps],
    )
