"""Orchestrator — thin façade over the LangGraph orchestration graph.

Q28 locked signature: run(request_text, notebook_id, on_event, context,
cancel_event). notebook_id is run-scoped truth: passed to every tool call
(especially rag.query) by the engine, never LLM-generated. context is
MemoryContext.as_prompt() text.

Owns exactly what should NOT live inside the graph:
  - trace-id minting (request correlation)
  - the per-request RunnableConfig: notebook_id, on_event callback,
    cancel_event
  - the OrchestrationError contract: a plan/validation failure routes to the
    graph's error terminal and is raised HERE, after the graph completes
    (fail-honest)

RIP port: multi-tenancy, quotas, approval store, reflection budget, and
Langfuse/audit scaffolding removed.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

from pydantic import BaseModel, Field

from app.agents.registry import AgentRegistry
from app.orchestration.aggregator import Aggregator
from app.orchestration.engine import OrchestrationState, build_orchestration_graph
from app.orchestration.plan import Plan
from app.orchestration.planner import Planner
from app.orchestration.results import StepResult
from app.orchestration.validator import PlanValidator
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration")


class OrchestrationError(Exception):
    """Raised when planning/validation fails before any step executes."""


class OrchestrationResult(BaseModel):
    trace_id: str
    plan_id: str
    goal: str
    step_results: list[StepResult]
    summary: str | None = None
    status: str = "failed"
    plan_incomplete: bool = True
    conflicts: list[str] = Field(default_factory=list)
    needs_clarification: bool = False

    @property
    def succeeded(self) -> bool:
        return all(r.status.value == "success" for r in self.step_results)


class Orchestrator:
    def __init__(
        self,
        planner: Planner,
        validator: PlanValidator,
        aggregator: Aggregator,
        registry: AgentRegistry,
        tool_registry: ToolRegistry | None = None,
    ):
        self._tool_registry = tool_registry or ToolRegistry()
        self._graph = build_orchestration_graph(
            planner,
            validator,
            registry,
            aggregator,
            tool_registry=self._tool_registry,
        )

    def run(
        self,
        request_text: str,
        notebook_id: str,
        on_event: Callable[[dict], None] | None = None,
        context: str | None = None,
        cancel_event: threading.Event | None = None,
        notebook_context: str | None = None,
    ) -> OrchestrationResult:
        logger.info("run started notebook=%s text=%.120s", notebook_id, request_text)
        trace_id = str(uuid.uuid4())

        # Per-request, per-thread dependencies ride in config (never state):
        # notebook_id for tool injection, on_event for step lifecycle,
        # cancel_event (nodes poll it cooperatively — threads cannot preempt
        # each other) — and thread_id, which keys the checkpointer's
        # snapshots for THIS run.
        config = {
            "configurable": {
                "thread_id": trace_id,
                "notebook_id": notebook_id,
                "on_event": on_event,
                "cancel_event": cancel_event,
            }
        }
        # Same add_node-style overload limitation as plan_graph.py: the
        # generic Pregel.invoke input cannot be matched from a plain dict
        # literal, though the runtime accepts this exact shape.
        final: OrchestrationState = self._graph.invoke(
            {
                "request_text": request_text,
                "notebook_id": notebook_id,
                "context": context,
                "notebook_context": notebook_context,
                "trace_id": trace_id,
                "plan": None,
                "plan_error": None,
                "step_results": {},
                "aggregation": None,
            },
            config=config,  # type: ignore[call-overload]
        )

        if final["plan_error"]:
            logger.warning("orchestration aborted: %s", final["plan_error"])
            raise OrchestrationError(final["plan_error"])

        plan: Plan | None = final["plan"]
        aggregation = final["aggregation"]
        if plan is None or aggregation is None:
            # Unreachable via graph routing; fail-honest rather than None-deref.
            raise OrchestrationError("orchestration graph finished without a plan")

        logger.info(
            "orchestrated plan=%s steps=%d status=%s trace=%s",
            plan.plan_id,
            len(plan.steps),
            aggregation.status,
            trace_id,
        )
        return OrchestrationResult(
            trace_id=trace_id,
            plan_id=plan.plan_id,
            goal=plan.goal,
            step_results=[final["step_results"][s.step_id] for s in plan.steps],
            summary=aggregation.summary,
            status=aggregation.status,
            plan_incomplete=aggregation.plan_incomplete,
            conflicts=aggregation.conflicts,
            needs_clarification=aggregation.needs_clarification,
        )
