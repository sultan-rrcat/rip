"""Unit tests for the extracted ReactFallback module.

These tests pin the fallback's contract: it runs ReAct, aggregates,
maps to OrchestrationResult, and returns the actionable error message
on failure. No real dependencies required.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from app.orchestration.react_fallback import ReactFallback
from app.orchestration.orchestrator import OrchestrationResult


def _make_fallback(
    *,
    react_result=None,
    react_side_effect=None,
    aggregate_status: str = "success",
) -> tuple[ReactFallback, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Create a ReactFallback with mock provider, agents, tools, aggregator."""
    provider = MagicMock()
    agents = MagicMock()
    agents.manifest.return_value = [{"agent_id": "reasoning"}]
    tools = MagicMock()
    tools.manifest.return_value = [{"tool_id": "rag.query"}]

    aggregator = MagicMock()
    if react_side_effect is not None:
        aggregator.aggregate.side_effect = react_side_effect
    else:
        aggregation = MagicMock()
        aggregation.status = aggregate_status
        aggregation.summary = "recovered answer"
        aggregation.plan_incomplete = False
        aggregation.conflicts = []
        aggregation.needs_clarification = False
        aggregation.shown = ["r1"]
        aggregation.hidden = []
        aggregation.visibility = {"r1": "show"}
        aggregator.aggregate.return_value = aggregation

    fallback = ReactFallback(provider, agents, tools, aggregator)
    return fallback, provider, agents, tools, aggregator


class TestReactFallbackSuccess:
    def test_returns_orchestration_result_on_success(self) -> None:
        fallback, provider, agents, tools, aggregator = _make_fallback(
            aggregate_status="success",
        )
        react_plan = MagicMock()
        react_plan.plan_id = "plan-123"
        react_plan.goal = "test request"
        react_result = MagicMock()
        react_result.step_results = []

        import app.orchestration.react_engine as re_mod
        original = re_mod.run_react
        re_mod.run_react = MagicMock(return_value=MagicMock(
            plan=react_plan, result=react_result,
        ))
        try:
            result, error = fallback.run(
                "test request",
                notebook_id="nb-1",
                trace_id="trace-1",
                plan_error="no builder applied",
            )
        finally:
            re_mod.run_react = original

        assert error is None
        assert isinstance(result, OrchestrationResult)
        assert result.plan_id == "plan-123"
        assert result.status == "success"

    def test_passes_all_parameters_to_run_react(self) -> None:
        fallback, provider, agents, tools, aggregator = _make_fallback()
        cancel_event = threading.Event()
        on_event = MagicMock()

        import app.orchestration.react_engine as re_mod
        original = re_mod.run_react
        mock_run = MagicMock(return_value=MagicMock(
            plan=MagicMock(plan_id="p", goal="g", steps=[]),
            result=MagicMock(step_results=[]),
        ))
        re_mod.run_react = mock_run
        try:
            fallback.run(
                "test request",
                notebook_id="nb-1",
                trace_id="trace-1",
                plan_error="no builder",
                context="ctx",
                notebook_context="nb-ctx",
                cancel_event=cancel_event,
                on_event=on_event,
                parent_span_ctx={"k": "v"},
            )
        finally:
            re_mod.run_react = original

        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["notebook_id"] == "nb-1"
        assert call_kwargs.kwargs["trace_id"] == "trace-1"
        assert call_kwargs.kwargs["context"] == "ctx"
        assert call_kwargs.kwargs["notebook_context"] == "nb-ctx"
        assert call_kwargs.kwargs["cancel_event"] is cancel_event
        assert call_kwargs.kwargs["on_event"] is on_event


class TestReactFallbackFailure:
    def test_returns_error_message_when_react_fails(self) -> None:
        fallback, provider, agents, tools, aggregator = _make_fallback(
            aggregate_status="failed",
        )
        aggregator.aggregate.return_value.summary = "Step r1 failed: timeout"

        import app.orchestration.react_engine as re_mod
        original = re_mod.run_react
        re_mod.run_react = MagicMock(return_value=MagicMock(
            plan=MagicMock(plan_id="p", goal="g", steps=[]),
            result=MagicMock(step_results=[]),
        ))
        try:
            result, error = fallback.run(
                "test request",
                notebook_id="nb-1",
                trace_id="trace-1",
                plan_error="no builder applied",
            )
        finally:
            re_mod.run_react = original

        assert result is None
        assert error is not None
        assert "Step r1 failed: timeout" in error

    def test_returns_plan_error_when_react_raises(self) -> None:
        fallback, provider, agents, tools, aggregator = _make_fallback()

        import app.orchestration.react_engine as re_mod
        original = re_mod.run_react
        re_mod.run_react = MagicMock(side_effect=RuntimeError("provider down"))
        try:
            result, error = fallback.run(
                "test request",
                notebook_id="nb-1",
                trace_id="trace-1",
                plan_error="no builder applied",
            )
        finally:
            re_mod.run_react = original

        assert result is None
        assert error == "no builder applied"

    def test_returns_react_summary_when_available(self) -> None:
        fallback, provider, agents, tools, aggregator = _make_fallback(
            aggregate_status="failed",
        )
        aggregator.aggregate.return_value.summary = "ReAct could not answer"

        import app.orchestration.react_engine as re_mod
        original = re_mod.run_react
        re_mod.run_react = MagicMock(return_value=MagicMock(
            plan=MagicMock(plan_id="p", goal="g", steps=[]),
            result=MagicMock(step_results=[]),
        ))
        try:
            result, error = fallback.run(
                "test request",
                notebook_id="nb-1",
                trace_id="trace-1",
                plan_error="no builder applied",
            )
        finally:
            re_mod.run_react = original

        assert result is None
        assert error == "ReAct could not answer"


class TestReactFallbackSpan:
    def test_manual_span_called_with_react_name(self) -> None:
        fallback, provider, agents, tools, aggregator = _make_fallback()

        import app.observability.langfuse as lf
        original_span = lf.manual_span
        mock_span = MagicMock()
        mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_span.return_value.__exit__ = MagicMock(return_value=False)
        lf.manual_span = mock_span

        import app.orchestration.react_engine as re_mod
        original_run = re_mod.run_react
        re_mod.run_react = MagicMock(return_value=MagicMock(
            plan=MagicMock(plan_id="p", goal="g", steps=[]),
            result=MagicMock(step_results=[]),
        ))
        try:
            fallback.run(
                "test request",
                notebook_id="nb-1",
                trace_id="trace-1",
                plan_error="no builder",
            )
        finally:
            lf.manual_span = original_span
            re_mod.run_react = original_run

        mock_span.assert_called_once()
        call_kwargs = mock_span.call_args
        assert call_kwargs.kwargs.get("name") == "react" or \
            (call_kwargs.args and call_kwargs.args[0] == "react") or \
            call_kwargs.get("as_type") == "span"