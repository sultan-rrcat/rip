"""Unit tests for the extracted PlanRouter module.

These tests pin the router's contract: L1 Router → L2 Builders →
Validator → plan node output. No real dependencies required.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from app.orchestration.plan import Plan, PlanStep
from app.orchestration.plan_router import PlanRouter, PlanRoutingResult
from app.orchestration.router import RouterResult
from app.orchestration.validator import PlanValidationError


def _make_router(
    *,
    route_result=None,
    build_result=None,
    build_side_effect=None,
    validate_side_effect=None,
) -> tuple[PlanRouter, MagicMock, MagicMock, MagicMock]:
    """Create a PlanRouter with mock provider, registry, validator."""
    provider = MagicMock()
    registry = MagicMock()
    registry.manifest.return_value = [{"agent_id": "reasoning"}]
    validator = MagicMock()
    if validate_side_effect is not None:
        validator.validate.side_effect = validate_side_effect

    router = PlanRouter(provider, registry, validator)
    return router, provider, registry, validator


def _make_route_result(intent="unknown", confidence=0.0):
    result = MagicMock()
    result.intent = MagicMock()
    result.intent.value = intent
    result.routed_by = "llm"
    result.confidence = confidence
    return result


class TestPlanRouterSuccess:
    def test_returns_plan_on_success(self) -> None:
        router, provider, registry, validator = _make_router()
        plan = Plan(
            plan_id="p1",
            goal="test",
            steps=[PlanStep(step_id="1", agent_id="reasoning",
                            input={"message": "x"}, expected_output_type="text")],
        )

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(return_value=_make_route_result("chat", 0.9))
        ))
        pr_mod.build_layered_plan = MagicMock(return_value=plan)
        try:
            result = router.route("test request", notebook_id="nb-1")
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert result.plan is plan
        assert result.plan_error is None
        assert result.route_info["intent"] == "chat"
        assert result.route_info["confidence"] == 0.9

    def test_returns_plan_error_when_no_builder(self) -> None:
        router, provider, registry, validator = _make_router()

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(return_value=_make_route_result("unknown", 0.0))
        ))
        pr_mod.build_layered_plan = MagicMock(return_value=None)
        try:
            result = router.route("test request", notebook_id="nb-1")
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert result.plan is None
        assert result.plan_error is not None
        assert "no deterministic builder" in result.plan_error

    def test_returns_plan_error_when_router_raises(self) -> None:
        router, provider, registry, validator = _make_router()

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(side_effect=RuntimeError("router down"))
        ))
        try:
            result = router.route("test request", notebook_id="nb-1")
        finally:
            pr_mod.Router = original_router

        assert result.plan is None
        assert "routing failed" in result.plan_error

    def test_returns_plan_error_when_validation_fails(self) -> None:
        router, provider, registry, validator = _make_router(
            validate_side_effect=PlanValidationError("bad plan")
        )
        plan = Plan(
            plan_id="p1",
            goal="test",
            steps=[PlanStep(step_id="1", agent_id="reasoning",
                            input={"message": "x"}, expected_output_type="text")],
        )

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(return_value=_make_route_result("chat", 0.9))
        ))
        pr_mod.build_layered_plan = MagicMock(return_value=plan)
        try:
            result = router.route("test request", notebook_id="nb-1")
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert result.plan is None
        assert "builder plan rejected" in result.plan_error


class TestPlanRouterTrivialPlan:
    def test_trivial_plan_repaired_to_reasoning_step(self) -> None:
        router, provider, registry, validator = _make_router()
        trivial_plan = Plan(plan_id="p1", goal="test", steps=[])

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(return_value=_make_route_result("chat", 0.9))
        ))
        pr_mod.build_layered_plan = MagicMock(return_value=trivial_plan)
        try:
            result = router.route("test request", notebook_id="nb-1")
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert result.plan is not None
        assert len(result.plan.steps) == 1
        assert result.plan.steps[0].agent_id == "reasoning"

    def test_trivial_plan_uses_first_agent_when_no_reasoning(self) -> None:
        router, provider, registry, validator = _make_router()
        registry.get.side_effect = KeyError("reasoning")
        registry.manifest.return_value = [{"agent_id": "coding"}]
        trivial_plan = Plan(plan_id="p1", goal="test", steps=[])

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(return_value=_make_route_result("chat", 0.9))
        ))
        pr_mod.build_layered_plan = MagicMock(return_value=trivial_plan)
        try:
            result = router.route("test request", notebook_id="nb-1")
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert result.plan is not None
        assert result.plan.steps[0].agent_id == "coding"


class TestPlanRouterSSE:
    def test_plan_event_emitted_on_success(self) -> None:
        router, provider, registry, validator = _make_router()
        plan = Plan(
            plan_id="p1",
            goal="test",
            steps=[PlanStep(step_id="1", agent_id="reasoning",
                            input={"message": "x"}, expected_output_type="text")],
        )
        events: list[dict] = []

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(return_value=_make_route_result("chat", 0.9))
        ))
        pr_mod.build_layered_plan = MagicMock(return_value=plan)
        try:
            router.route(
                "test request", notebook_id="nb-1",
                on_event=events.append,
            )
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert len(events) == 1
        assert events[0]["type"] == "plan"
        assert events[0]["plan_id"] == "p1"
        assert events[0]["attempt"] == 1
        assert events[0]["route"]["intent"] == "chat"

    def test_no_event_on_failure(self) -> None:
        router, provider, registry, validator = _make_router()
        events: list[dict] = []

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        pr_mod.Router = MagicMock(return_value=MagicMock(
            route=MagicMock(return_value=_make_route_result("unknown", 0.0))
        ))
        pr_mod.build_layered_plan = MagicMock(return_value=None)
        try:
            router.route(
                "test request", notebook_id="nb-1",
                on_event=events.append,
            )
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert len(events) == 0


class TestPlanRouterCancel:
    def test_cancel_event_forwarded(self) -> None:
        router, provider, registry, validator = _make_router()
        cancel = threading.Event()

        import app.orchestration.plan_router as pr_mod
        original_router = pr_mod.Router
        original_build = pr_mod.build_layered_plan
        mock_route = MagicMock(return_value=_make_route_result("chat", 0.9))
        pr_mod.Router = MagicMock(return_value=MagicMock(route=mock_route))
        pr_mod.build_layered_plan = MagicMock(return_value=None)
        try:
            router.route(
                "test request", notebook_id="nb-1",
                cancel_event=cancel,
            )
        finally:
            pr_mod.Router = original_router
            pr_mod.build_layered_plan = original_build

        assert mock_route.call_args.kwargs["cancel_event"] is cancel