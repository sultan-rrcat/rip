"""Outer orchestration graph — the LangGraph engine.

The whole round trip is one compiled StateGraph:

    START ──► plan ──(builder hit?)──► execute ──► aggregate ──► END
                      │ (miss → plan_error → END; the orchestrator
                      │  runs L3 ReAct before failing honest)

Planning is L1 → L2 → L3:

- plan node: L1 Router (sole dispatcher, every request via LLM) + L2
  deterministic builders + Validator. Builder misses (unknown intent,
  non-deterministic shapes, unresolvable converts, >5 files) return
  plan_error so the orchestrator runs L3 ReAct. Router failures fail
  open the same way. No mega-prompt, no planner recall.
- execute node: builds + streams the per-request inner plan graph
  (plan_graph.run_plan_graph), forwarding step events to on_event
- aggregate node: deterministic Aggregator (Q36, no LLM), no retry —
  partial/failed runs surface honestly; clarifications are the answer.

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

from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.observability.langfuse import get_trace_context, manual_span, truncate
from app.orchestration.aggregator import AggregationResult, Aggregator
from app.orchestration.plan import Plan  # noqa: F401 — used in OrchestrationState type hint
from app.orchestration.plan_graph import run_plan_graph
from app.orchestration.plan_router import PlanRouter
from app.orchestration.planner import Planner
from app.orchestration.results import ExecutionResult, StepResult
from app.orchestration.validator import PlanValidator
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
    #: Explicit Langfuse parent for `step:{id}` spans: the plan-span context
    #: ({"trace_id", "parent_span_id"}), captured inside the plan span.
    #: None when tracing is off or the plan failed. Lets steps nest INSIDE
    #: `plan` instead of sitting beside it under `run`.
    plan_span_ctx: dict[str, str] | None


def _configurable(config: RunnableConfig) -> dict:
    return config.get("configurable") or {}


def _make_plan_node(
    plan_router: PlanRouter,
) -> Callable:
    def plan_node(state: OrchestrationState, config: RunnableConfig) -> dict:
        # Cooperative cancel: a cancelled run never spends another LLM call
        # on planning; the error terminal unwinds the graph.
        if _cancelled(config):
            return {"plan": None, "plan_error": "run cancelled"}
        # Forced ReAct: skip L1 Router + L2 builders entirely (no LLM call,
        # no router span). plan_error routes the run to the orchestrator's
        # existing L3 ReAct fallback.
        if settings.force_react:
            logger.info("force_react enabled, skipping router+builders → L3 ReAct")
            return {
                "plan": None,
                "plan_error": "force_react enabled — L3 ReAct required",
                "plan_span_ctx": None,
            }
        result = plan_router.route(
            state["request_text"],
            context=state.get("context"),
            notebook_context=state.get("notebook_context"),
            notebook_id=state.get("notebook_id"),
            cancel_event=_cancel_event(config),
            on_event=_configurable(config).get(_ON_EVENT),
            trace_context=_configurable(config).get(_TRACE_CTX),
        )
        return {
            "plan": result.plan,
            "plan_error": result.plan_error,
            "plan_span_ctx": result.plan_span_ctx,
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
            agg_obs.update(output={
                "status": agg.status,
                "summary": truncate(agg.summary, 2000),
                "shown": list(agg.shown),
                "hidden": list(agg.hidden),
                "visibility": dict(agg.visibility),
            })
        return {"aggregation": agg}

    return aggregate_node


def _route_after_plan(state: OrchestrationState) -> str:
    if state["plan_error"] or state["plan"] is None:
        return END
    return "execute"


def _route_after_aggregate(state: OrchestrationState) -> str:
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
    plan_router = PlanRouter(planner.provider, registry, validator)
    graph = StateGraph(OrchestrationState)
    graph.add_node(
        "plan", _make_plan_node(plan_router),
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
        {"execute": "execute", END: END},
    )
    graph.add_edge("execute", "aggregate")
    graph.add_edge("aggregate", END)
    # Checkpointed state: every super-step writes a snapshot keyed by
    # thread_id (= trace_id, set by the façade). MemorySaver is in-process —
    # run durability across processes comes from Postgres run_events (Phase 4);
    # per-request isolation is via the trace id.
    return graph.compile(checkpointer=MemorySaver())
