"""Outer orchestration graph — the LangGraph engine.

The whole round trip is one compiled StateGraph:

    START ──► plan ──(plan ok?)──► execute ──► aggregate ──► END
                    │                  (fail-honest: a rejected plan
                    └─(plan_error, ─► plan (once)   never reaches execution
                       attempts left)              unless retried)

Bounded planner recall (one retry = two plans max): a rejected plan or a
partial/failed aggregation routes back to `plan` once with short,
instance-specific feedback; the second failure surfaces honestly.
Clarifications never replan. No resumption: retries re-execute fully.

- plan node: Planner (+ retry feedback) + Validator
- execute node: builds + streams the per-request inner plan graph
  (plan_graph.run_plan_graph), forwarding step events to on_event
- aggregate node: deterministic Aggregator (Q36, no LLM)

Per-request state that must NOT live in graph state:
- `on_event` (per-request callback) and `notebook_id` (run-scoped truth
  injected into tool inputs) travel via RunnableConfig["configurable"] —
  config is per-invocation and never checkpointed, unlike state.

Langfuse parenting (explicit, thread-safe): the orchestrator captures the
run-span context into config["configurable"]["trace_context"]; plan and
aggregate spans parent explicitly under it. The plan node captures its own
span context into state["plan_span_ctx"]; the execute node forwards it to
the inner plan graph so `step:{id}` spans nest INSIDE `plan`
(run → plan → step, aggregate sibling of plan). Plain-string IDs only —
checkpoint-safe, no contextvars dependence across LangGraph threads.

RIP port: approvals, reflection, and correlation helpers removed;
cooperative cancel kept.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.base import StepStatus
from app.agents.registry import AgentRegistry
from app.observability.langfuse import get_trace_context, manual_span, truncate
from app.orchestration.aggregator import AggregationResult, Aggregator
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.plan_graph import run_plan_graph
from app.orchestration.planner import Planner
from app.orchestration.results import ExecutionResult, StepResult
from app.orchestration.validator import PlanValidationError, PlanValidator
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.engine")

_ON_EVENT = "on_event"
_NOTEBOOK_ID = "notebook_id"
_CANCEL_EVENT = "cancel_event"
#: Captured run-span context ({"trace_id", "parent_span_id"}) injected by
#: the orchestrator so outer nodes parent under `run` even when LangGraph
#: schedules them on pool threads without the worker's contextvars.
_TRACE_CTX = "trace_context"


def _cancel_event(config: RunnableConfig) -> threading.Event | None:
    """The run's cooperative cancel flag (None when run without one)."""
    return _configurable(config).get(_CANCEL_EVENT)


def _cancelled(config: RunnableConfig) -> bool:
    event = _cancel_event(config)
    return event is not None and event.is_set()


#: Retry budget: one recall = two planner outputs max per run.
_MAX_PLAN_ATTEMPTS = 2


class OrchestrationState(TypedDict):
    """Outer graph state — serializable, checkpoint-safe."""

    request_text: str
    notebook_id: str | None
    context: str | None
    notebook_context: str | None
    trace_id: str
    plan: Plan | None
    plan_error: str | None
    step_results: dict[str, StepResult]
    aggregation: AggregationResult | None
    attempt: int
    planner_feedback: str | None
    #: Explicit Langfuse parent for `step:{id}` spans: the current attempt's
    #: plan-span context ({"trace_id", "parent_span_id"}), captured inside
    #: the plan span. None when tracing is off or the plan failed. Lets
    #: steps nest INSIDE `plan` instead of sitting beside it under `run`.
    plan_span_ctx: dict[str, str] | None


def _configurable(config: RunnableConfig) -> dict:
    return config.get("configurable") or {}


def _validation_feedback(plan_dump: str | None, error: str) -> str:
    """Short, instance-specific retry signal for a rejected plan.

    A raw JSON dump gets truncated before the malformation (observed live:
    the dangling `"step_id": "3"` element sat past the 800-char cut), so
    lead with a per-step skeleton that always fits: step ids, executors and
    input keys expose nesting/stray-element slips at a glance.
    """
    parts = [f"Your previous plan was REJECTED: {error}"]
    if plan_dump:
        skeleton = _plan_skeleton(plan_dump)
        parts.append(
            f"Rejected plan skeleton (fix it, do not repeat it): {skeleton[:600]}"
        )
    parts.append("Return a corrected plan satisfying every rule above.")
    return "\n".join(parts)


def _plan_skeleton(plan_dump: str) -> str:
    """One line per step: id, executor, input keys. Never raises."""
    import json as _json

    try:
        data = _json.loads(plan_dump)
        steps = data.get("steps", [])
    except (ValueError, AttributeError, TypeError):
        return plan_dump[:600]
    lines: list[str] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            lines.append(f"[{i}] NOT AN OBJECT: {str(s)[:100]!r}")
            continue
        ex = s.get("agent_id") or s.get("tool_id") or "NO-EXECUTOR"
        raw_input = s.get("input")
        keys = sorted(raw_input) if isinstance(raw_input, dict) else type(raw_input).__name__
        lines.append(
            f"step {s.get('step_id', '?')}: executor={ex} input_keys={keys} "
            f"depends_on={s.get('depends_on', 'MISSING')}"
        )
    return "; ".join(lines) or plan_dump[:600]


def _execution_feedback(plan_dump: str, failed: list[StepResult]) -> str:
    """Short retry signal for a partial/failed execution."""
    lines = [
        f"- step {r.step_id} ({r.agent_id}): {r.error or 'unknown error'}"
        + (f" [output was: {r.output[:200]}]" if r.output else "")
        for r in failed
    ]
    return (
        "Your previous plan executed with FAILURES:\n" + "\n".join(lines) + "\n"
        f"Previous plan (keep what worked, change only what the errors implicate): {plan_dump[:800]}\n"
        "Return a corrected plan satisfying every rule above."
    )


def _make_plan_node(
    planner: Planner, validator: PlanValidator, registry: AgentRegistry
) -> Callable:
    def plan_node(state: OrchestrationState, config: RunnableConfig) -> dict:
        # Cooperative cancel: a cancelled run never spends another LLM call
        # on planning; the error terminal unwinds the graph.
        if _cancelled(config):
            return {"plan": None, "plan_error": "run cancelled"}
        attempt = int(state.get("attempt") or 0) + 1
        feedback_in = state.get("planner_feedback")
        run_ctx = _configurable(config).get(_TRACE_CTX)
        with manual_span(
            "plan", as_type="span",
            input={
                "request": truncate(state["request_text"], 2000),
                "attempt": attempt,
                "feedback": truncate(feedback_in, 800),
            },
            trace_context=run_ctx,
        ) as plan_obs:
            rejected: Plan | None = None
            try:
                plan = planner.plan(
                    state["request_text"],
                    context=state["context"],
                    notebook_context=state.get("notebook_context"),
                    feedback=feedback_in,
                )
                rejected = plan
                validator.validate(plan)
                if plan.is_trivial():
                    # Defensive fallback: the planner prompt forbids empty
                    # plans, but older models / cached outputs can still emit
                    # steps=[]. An empty DAG would execute zero steps and the
                    # deterministic aggregator would report failed ("No steps
                    # were executed."). Route trivial requests to one
                    # conversational reasoning step instead.
                    fallback_id = "reasoning"
                    try:
                        registry.get(fallback_id)
                    except KeyError:
                        manifest = registry.manifest()
                        if not manifest:
                            raise PlanValidationError(
                                "trivial plan has no steps and no agents registered"
                            )
                        fallback_id = manifest[0]["agent_id"]
                    plan = Plan(
                        plan_id=plan.plan_id,
                        goal=plan.goal,
                        steps=[
                            PlanStep(
                                step_id="1",
                                agent_id=fallback_id,
                                input={"message": state["request_text"]},
                                expected_output_type="text",
                            )
                        ],
                    )
                    validator.validate(plan)
                    logger.info(
                        "trivial plan repaired plan=%s fallback_agent=%s",
                        plan.plan_id, fallback_id,
                    )
            except (ValueError, PlanValidationError) as e:
                err = str(e)
                plan_obs.update(output={"error": err[:500], "attempt": attempt})
                if attempt < _MAX_PLAN_ATTEMPTS:
                    dump = rejected.model_dump_json()[:2000] if rejected else None
                    logger.info(
                        "plan %s rejected (attempt %d), recalling planner",
                        rejected.plan_id if rejected else "?",
                        attempt,
                    )
                    return {
                        "plan": None,
                        "plan_error": None,
                        "attempt": attempt,
                        "planner_feedback": _validation_feedback(dump, err),
                    }
                return {
                    "plan": None,
                    "plan_error": err,
                    "attempt": attempt,
                    "planner_feedback": None,
                }
            plan_obs.update(output={
                "goal": truncate(plan.goal, 500),
                "steps": len(plan.steps),
                "executors": [s.executor_id for s in plan.steps],
                "attempt": attempt,
            })
            # Capture this attempt's plan-span context while it is current so
            # the execute node can parent step:{id} spans explicitly under it.
            # Stored in graph state (plain strings) — survives checkpointing
            # and thread hops where contextvars would be lost.
            plan_span_ctx = get_trace_context()
        # The worker persists + streams this as the SSE `plan` event. Emitted
        # here (plan-time, before any step runs) so live subscribers and the
        # persisted replay both see run_started -> plan -> step_* in order.
        # Additive and None-safe: callers without on_event see no change.
        on_event = _configurable(config).get(_ON_EVENT)
        if callable(on_event):
            on_event(
                {
                    "type": "plan",
                    "plan_id": plan.plan_id,
                    "goal": plan.goal,
                    "attempt": attempt,
                    "steps": [
                        {
                            "step_id": s.step_id,
                            "executor": s.executor_id,
                            "depends_on": list(s.depends_on),
                            "expected_output_type": s.expected_output_type,
                        }
                        for s in plan.steps
                    ],
                }
            )
        return {
            "plan": plan,
            "plan_error": None,
            "attempt": attempt,
            "planner_feedback": None,
            "plan_span_ctx": plan_span_ctx,
        }

    return plan_node


def _make_execute_node(
    registry: AgentRegistry, tool_registry: ToolRegistry | None = None
) -> Callable:
    def execute_node(state: OrchestrationState, config: RunnableConfig) -> dict:
        plan = state["plan"]
        if plan is None:  # unreachable via routing; defensive
            return {}

        on_event = _configurable(config).get(_ON_EVENT)
        if not callable(on_event):
            on_event = None

        exec_result = run_plan_graph(
            plan,
            registry,
            tool_registry=tool_registry or ToolRegistry(),
            trace_id=state["trace_id"],
            notebook_id=state["notebook_id"],
            context=state["context"],
            fallback_message=state["request_text"],
            on_event=on_event,
            cancel_event=_cancel_event(config),
            parent_span_ctx=state.get("plan_span_ctx"),
        )
        results = {r.step_id: r for r in exec_result.step_results}
        return {"step_results": results}

    return execute_node


def _make_aggregate_node(aggregator: Aggregator) -> Callable:
    def aggregate_node(state: OrchestrationState, config: RunnableConfig) -> dict:
        plan = state["plan"]
        if plan is None:  # unreachable via routing; defensive
            return {}

        exec_result = ExecutionResult(
            trace_id=state["trace_id"],
            step_results=[state["step_results"][s.step_id] for s in plan.steps],
        )
        run_ctx = _configurable(config).get(_TRACE_CTX)
        with manual_span(
            "aggregate", as_type="span", input={"goal": truncate(plan.goal, 500)},
            trace_context=run_ctx,
        ) as agg_obs:
            agg = aggregator.aggregate(plan, exec_result)
            # One execution retry: partial/failed runs replan with the step
            # errors as feedback. Clarifications never replan (the question IS
            # the answer); successes, cancellations and capped attempts end.
            if _cancelled(config):
                agg_obs.update(output={
                    "status": agg.status,
                    "summary": truncate(agg.summary, 2000),
                    "shown": list(agg.shown),
                    "hidden": list(agg.hidden),
                    "visibility": dict(agg.visibility),
                })
                return {"aggregation": agg, "planner_feedback": None}
            attempt = int(state.get("attempt") or 0)
            retry_armed = (
                agg.status in ("partial", "failed")
                and not agg.needs_clarification
                and attempt < _MAX_PLAN_ATTEMPTS
            )
            agg_obs.update(output={
                "status": agg.status,
                "summary": truncate(agg.summary, 2000),
                "retry_armed": retry_armed,
                "shown": list(agg.shown),
                "hidden": list(agg.hidden),
                "visibility": dict(agg.visibility),
            })
        # One execution retry: partial/failed runs replan with the step
        # errors as feedback. Clarifications never replan (the question IS
        # the answer); successes, cancellations and capped attempts end.
        if _cancelled(config):
            return {"aggregation": agg, "planner_feedback": None}
        attempt = int(state.get("attempt") or 0)
        retry_armed = (
            agg.status in ("partial", "failed")
            and not agg.needs_clarification
            and attempt < _MAX_PLAN_ATTEMPTS
        )
        if retry_armed:
            failed = [r for r in exec_result.step_results if r.status is not StepStatus.SUCCESS]
            logger.info(
                "aggregation %s (attempt %d), recalling planner",
                agg.status, attempt,
            )
            return {
                "aggregation": agg,
                "planner_feedback": _execution_feedback(
                    plan.model_dump_json(), failed,
                ),
            }
        return {"aggregation": agg, "planner_feedback": None}

    return aggregate_node


def _route_after_plan(state: OrchestrationState) -> str:
    if state["plan_error"]:
        return END
    if state["plan"] is None:
        return "plan"  # retry loop: rejected plan, feedback armed
    return "execute"


def _route_after_aggregate(state: OrchestrationState) -> str:
    if state.get("planner_feedback"):
        return "plan"  # execution retry armed
    return END


def build_orchestration_graph(
    planner: Planner,
    validator: PlanValidator,
    registry: AgentRegistry,
    aggregator: Aggregator,
    *,
    tool_registry: ToolRegistry | None = None,
) -> CompiledStateGraph:
    """Compile the outer graph once per Orchestrator (process-wide)."""
    graph = StateGraph(OrchestrationState)
    graph.add_node(
        "plan", _make_plan_node(planner, validator, registry),
        input_schema=OrchestrationState,  # type: ignore[call-overload]
    )
    graph.add_node(
        "execute", _make_execute_node(registry, tool_registry),
        input_schema=OrchestrationState,  # type: ignore[call-overload]
    )
    graph.add_node(
        "aggregate", _make_aggregate_node(aggregator),
        input_schema=OrchestrationState,  # type: ignore[call-overload]
    )
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", _route_after_plan,
        {"plan": "plan", "execute": "execute", END: END},
    )
    graph.add_edge("execute", "aggregate")
    graph.add_conditional_edges(
        "aggregate", _route_after_aggregate, {"plan": "plan", END: END},
    )
    # Checkpointed state: every super-step writes a snapshot keyed by
    # thread_id (= trace_id, set by the façade). MemorySaver is in-process —
    # run durability across processes comes from Postgres run_events (Phase 4);
    # per-request isolation is via the trace id.
    return graph.compile(checkpointer=MemorySaver())
