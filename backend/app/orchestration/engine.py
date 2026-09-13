"""Outer orchestration graph — the LangGraph engine.

The whole round trip is one compiled StateGraph:

    START ──► plan ──(plan ok?)──► execute ──► aggregate ──► END
                    │                  (fail-honest: a rejected plan
                    └─(plan_error)──► END   never reaches execution)

No reflection loop, no retry replanning: step failures are returned
honestly and the deterministic aggregator reports them (merge decision).

- plan node: Planner + Validator
- execute node: builds + streams the per-request inner plan graph
  (plan_graph.run_plan_graph), forwarding step events to on_event
- aggregate node: deterministic Aggregator (Q36, no LLM)

Per-request state that must NOT live in graph state:
- `on_event` (per-request callback) and `notebook_id` (run-scoped truth
  injected into tool inputs) travel via RunnableConfig["configurable"] —
  config is per-invocation and never checkpointed, unlike state.

RIP port: approvals, reflection, Langfuse spans, and correlation helpers
removed; cooperative cancel kept.
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
from app.orchestration.aggregator import AggregationResult, Aggregator
from app.orchestration.plan import Plan
from app.orchestration.plan_graph import run_plan_graph
from app.orchestration.planner import Planner
from app.orchestration.results import ExecutionResult, StepResult
from app.orchestration.validator import PlanValidationError, PlanValidator
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.engine")

_ON_EVENT = "on_event"
_NOTEBOOK_ID = "notebook_id"
_CANCEL_EVENT = "cancel_event"


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
    trace_id: str
    plan: Plan | None
    plan_error: str | None
    step_results: dict[str, StepResult]
    aggregation: AggregationResult | None


def _configurable(config: RunnableConfig) -> dict:
    return config.get("configurable") or {}


def _make_plan_node(planner: Planner, validator: PlanValidator) -> Callable:
    def plan_node(state: OrchestrationState, config: RunnableConfig) -> dict:
        # Cooperative cancel: a cancelled run never spends another LLM call
        # on planning; the error terminal unwinds the graph.
        if _cancelled(config):
            return {"plan": None, "plan_error": "run cancelled"}
        try:
            plan = planner.plan(state["request_text"], context=state["context"])
            validator.validate(plan)
        except (ValueError, PlanValidationError) as e:
            return {"plan": None, "plan_error": str(e)}
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
                    "steps": [
                        {
                            "step_id": s.step_id,
                            "executor": s.executor_id,
                            "depends_on": list(s.depends_on),
                        }
                        for s in plan.steps
                    ],
                }
            )
        return {"plan": plan, "plan_error": None}

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
        agg = aggregator.aggregate(plan, exec_result)
        return {"aggregation": agg}

    return aggregate_node


def _route_after_plan(state: OrchestrationState) -> str:
    return END if state["plan_error"] else "execute"


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
        "plan", _make_plan_node(planner, validator),
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
    graph.add_conditional_edges("plan", _route_after_plan, {"execute": "execute", END: END})
    graph.add_edge("execute", "aggregate")
    graph.add_edge("aggregate", END)
    # Checkpointed state: every super-step writes a snapshot keyed by
    # thread_id (= trace_id, set by the façade). MemorySaver is in-process —
    # run durability across processes comes from Postgres run_events (Phase 4);
    # per-request isolation is via the trace id.
    return graph.compile(checkpointer=MemorySaver())
