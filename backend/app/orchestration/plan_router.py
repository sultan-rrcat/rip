"""PlanRouter — L1 Router → L2 Builders → Validator → plan node output.

Extracted from `engine._make_plan_node()` so the routing logic is
independently testable and the graph node is thin wiring.

The router owns:
  - the L1 Router call (one cheap `generate_structured` per request)
  - the L2 Builder dispatch (deterministic DAGs)
  - the Validator gate (grounding, cycles, step budget)
  - trivial plan repair (empty plans → single reasoning step)
  - the `router` + `plan` spans (trace-only siblings under `run`)
  - SSE `plan` event emission
  - plan_span_ctx capture (so the execute node parents step spans)

The graph node owns:
  - checking cancellation
  - checking `force_react`
  - delegating to PlanRouter
  - returning the state dict
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TypedDict

from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.observability.langfuse import get_trace_context, manual_span, truncate
from app.orchestration.builders import build as build_layered_plan
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.router import Router
from app.orchestration.validator import PlanValidationError, PlanValidator  # noqa: F401

logger = logging.getLogger("orchestration.engine")


class RouteInfo(TypedDict):
    layer: str
    intent: str
    routed_by: str
    confidence: float
    queries: list


@dataclass
class PlanRoutingResult:
    plan: Plan | None
    plan_error: str | None
    plan_span_ctx: dict[str, str] | None
    route_info: RouteInfo = field(default_factory=lambda: {
        "layer": "L3-react",
        "intent": "unknown",
        "routed_by": "none",
        "confidence": 0.0,
        "queries": [],
    })


class PlanRouter:
    """Routes a request through L1 Router → L2 Builders → Validator.

    Composition-time dependencies (provider, registry, validator) are
    bound in the constructor; per-request values ride on `route()`.
    """

    def __init__(
        self,
        provider,
        registry: AgentRegistry,
        validator: PlanValidator,
    ):
        self._provider = provider
        self._registry = registry
        self._validator = validator

    def route(
        self,
        request_text: str,
        *,
        context: str | None = None,
        notebook_context: str | None = None,
        notebook_id: str | None = None,
        cancel_event=None,
        on_event=None,
        trace_context: dict[str, str] | None = None,
    ) -> PlanRoutingResult:
        """Route a request to a validated Plan (or plan_error).

        Returns a `PlanRoutingResult` with:
        - `plan` + `plan_error`: mutually exclusive (one is None)
        - `plan_span_ctx`: the plan-span context for step parenting
        - `route_info`: intent/confidence/routed_by for SSE + spans
        """
        run_ctx = trace_context
        route_info: RouteInfo = {
            "layer": "L3-react",
            "intent": "unknown",
            "routed_by": "none",
            "confidence": 0.0,
            "queries": [],
        }

        # L1 Router (sole dispatcher) → L2 Builders. Any miss (unknown intent,
        # non-deterministic shape, unresolvable convert, >5 files, validation
        # failure, router error) returns plan_error so the orchestrator runs
        # L3 ReAct before failing honest.
        candidate: Plan | None = None
        with manual_span(
            "router",
            as_type="span",
            input={
                "request": truncate(request_text, 2000),
                "notebook_context": truncate(notebook_context, 500),
            },
            trace_context=run_ctx,
        ) as router_obs:
            try:
                route = Router(self._provider).route(
                    request_text, context=context,
                    cancel_event=cancel_event,
                    notebook_context=notebook_context,
                )
                route_info = {
                    "layer": "L2-builder",
                    "intent": route.intent.value,
                    "routed_by": route.routed_by,
                    "confidence": route.confidence,
                }
                router_obs.update(output={
                    "intent": route.intent.value,
                    "confidence": route.confidence,
                    "routed_by": route.routed_by,
                })
                candidate = build_layered_plan(
                    request_text,
                    route,
                    notebook_context,
                    notebook_id,
                )
                if candidate is None:
                    logger.info(
                        "no builder for intent=%s, delegating to L3 ReAct",
                        route.intent.value,
                    )
                    return PlanRoutingResult(
                        plan=None,
                        plan_error=(
                            f"no deterministic builder for intent "
                            f"{route.intent.value} — L3 ReAct required"
                        ),
                        plan_span_ctx=None,
                        route_info=route_info,
                    )
            except Exception as e:  # noqa: BLE001 - miss fails open to L3 ReAct
                router_obs.update(output={"error": str(e)[:500]})
                logger.info("router/builder miss, delegating to L3 ReAct: %s", e)
                return PlanRoutingResult(
                    plan=None,
                    plan_error=f"routing failed ({e}) — L3 ReAct required",
                    plan_span_ctx=None,
                    route_info=route_info,
                )

        with manual_span(
            "plan", as_type="span",
            input={"request": truncate(request_text, 2000)},
            trace_context=run_ctx,
        ) as plan_obs:
            try:
                plan = candidate
                self._validator.validate(plan)
                if plan.is_trivial():
                    # Defensive fallback: builders always emit steps, but an
                    # empty plan would execute zero steps and the
                    # deterministic aggregator would report failed ("No steps
                    # were executed."). Route to one conversational
                    # reasoning step instead.
                    fallback_id = "reasoning"
                    try:
                        self._registry.get(fallback_id)
                    except KeyError:
                        manifest = self._registry.manifest()
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
                                input={"message": request_text},
                                expected_output_type="text",
                            )
                        ],
                    )
                    self._validator.validate(plan)
                    logger.info(
                        "trivial plan repaired plan=%s fallback_agent=%s",
                        plan.plan_id, fallback_id,
                    )
            except (ValueError, PlanValidationError) as e:
                err = str(e)
                plan_obs.update(output={
                    "error": err[:500],
                    "layer": route_info["layer"],
                    "intent": route_info["intent"],
                    "routed_by": route_info["routed_by"],
                })
                logger.info("builder plan rejected, delegating to L3 ReAct: %s", err)
                return PlanRoutingResult(
                    plan=None,
                    plan_error=f"builder plan rejected ({err}) — L3 ReAct required",
                    plan_span_ctx=None,
                    route_info=route_info,
                )
            plan_obs.update(output={
                "goal": truncate(plan.goal, 500),
                "steps": len(plan.steps),
                "executors": [s.executor_id for s in plan.steps],
                "layer": route_info["layer"],
                "intent": route_info["intent"],
                "routed_by": route_info["routed_by"],
                "confidence": route_info["confidence"],
            })
            # Capture the plan-span context while it is current so the
            # execute node can parent step:{id} spans explicitly under it.
            # Stored in graph state (plain strings) — survives checkpointing
            # and thread hops where contextvars would be lost.
            plan_span_ctx = get_trace_context()

        # SSE `plan` event: emitted here (plan-time, before any step runs)
        # so live subscribers and the persisted replay both see
        # run_started → plan → step_* in order. Additive and None-safe:
        # callers without on_event see no change. `attempt` stays 1 forever
        # (no recall): the frontend uses it to detect retried plans, and a
        # constant keeps old clients working.
        if callable(on_event):
            on_event({
                "type": "plan",
                "plan_id": plan.plan_id,
                "goal": plan.goal,
                "attempt": 1,
                "route": {
                    "intent": route_info["intent"],
                    "routed_by": route_info["routed_by"],
                    "confidence": route_info["confidence"],
                },
                "steps": [
                    {
                        "step_id": s.step_id,
                        "executor": s.executor_id,
                        "depends_on": list(s.depends_on),
                        "expected_output_type": s.expected_output_type,
                    }
                    for s in plan.steps
                ],
            })

        return PlanRoutingResult(
            plan=plan,
            plan_error=None,
            plan_span_ctx=plan_span_ctx,
            route_info=route_info,
        )