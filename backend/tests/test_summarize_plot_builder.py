"""ADR-041 — summarize_plot deterministic builder + plot.chart 'data' input."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app.orchestration.intents import DETERMINISTIC_INTENTS, Intent
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.validator import PlanValidationError, PlanValidator
from app.tools.base import ToolRequest
from app.tools.plot_chart import PlotChartTool, _parse_chart_data


def test_summarize_plot_is_deterministic() -> None:
    assert Intent.SUMMARIZE_PLOT in DETERMINISTIC_INTENTS


def test_plot_tool_accepts_data_json() -> None:
    tool = PlotChartTool()
    resp = tool.execute(
        ToolRequest(
            tool_id="plot.chart",
            input={
                "chart_type": "bar",
                "data": '{"labels": ["Alpha", "Beta"], "values": [10, 20.5]}',
                "title": "Benchmarks",
            },
        )
    )
    assert resp.ok, resp.error
    assert resp.data["point_count"] == 2
    assert "Alpha" in resp.output


def test_plot_tool_accepts_fenced_and_prose_wrapped_json() -> None:
    tool = PlotChartTool()
    fenced = tool.execute(
        ToolRequest(
            tool_id="plot.chart",
            input={
                "chart_type": "line",
                "data": '```json\n{"labels": ["A", "B"], "values": [1, 2]}\n```',
            },
        )
    )
    assert fenced.ok, fenced.error
    prose = tool.execute(
        ToolRequest(
            tool_id="plot.chart",
            input={
                "chart_type": "line",
                "data": 'Here you go: {"labels": ["A", "B"], "values": [1, 2]}',
            },
        )
    )
    assert prose.ok, prose.error


def test_plot_tool_rejects_bad_data_honestly() -> None:
    tool = PlotChartTool()
    for bad in (
        "not json",
        '{"labels": ["A"], "values": [1, 2]}',
        '{"labels": [], "values": []}',
        '{"labels": ["A"], "values": ["x"]}',
        [1, 2, 3],
    ):
        resp = tool.execute(
            ToolRequest(tool_id="plot.chart", input={"chart_type": "bar", "data": bad})
        )
        assert not resp.ok, bad
        assert resp.error


def test_plot_tool_data_rejects_mixing_with_values() -> None:
    tool = PlotChartTool()
    resp = tool.execute(
        ToolRequest(
            tool_id="plot.chart",
            input={"chart_type": "bar", "data": "{}", "values": [1, 2], "labels": ["a", "b"]},
        )
    )
    assert not resp.ok


def test_parse_chart_data_dict_passthrough() -> None:
    assert _parse_chart_data({"labels": ["a"], "values": [1]}) == (["a"], [1.0])


def _plot_plan(data_input: object, depends_on: list[str], extract_eot: str) -> Plan:
    return Plan(
        plan_id="p",
        goal="g",
        steps=[
            PlanStep(
                step_id="1",
                tool_id="rag.query",
                input={"query": "q"},
                expected_output_type="chunks",
            ),
            PlanStep(
                step_id="2",
                agent_id="reasoning",
                input={"message": "extract from {{1}}"},
                depends_on=["1"],
                expected_output_type=extract_eot,
            ),
            PlanStep(
                step_id="3",
                tool_id="plot.chart",
                input={"data": data_input, "chart_type": "bar", "title": "t"},
                depends_on=depends_on,
                expected_output_type="chart",
            ),
        ],
    )


def _validator() -> PlanValidator:
    from app.agents.reasoning import ReasoningAgent
    from app.providers.base import ModelProvider

    class _FakeProvider(ModelProvider):
        def generate(self, model, messages, *, temperature=0.2, max_tokens=None, cancel_event=None):
            return "ok"

        def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None):
            return {}

        def embed(self, model: str, text: str) -> list[float]:
            raise NotImplementedError("test fake")

        def list_available_models(self) -> list[dict]:
            return [{"id": "fake"}]

    from app.agents.registry import AgentRegistry
    from app.tools.registry import get_default_tool_registry

    agents = AgentRegistry()
    agents.register(ReasoningAgent(_FakeProvider()))
    return PlanValidator(agents, get_default_tool_registry())


def test_validator_accepts_data_placeholder_to_numbers_step() -> None:
    _validator().validate(_plot_plan("{{2}}", ["2"], "numbers"))


def test_validator_rejects_data_placeholder_to_answer_step() -> None:
    with pytest.raises(PlanValidationError, match="numbers-producing step"):
        _validator().validate(_plot_plan("{{2}}", ["2"], "answer"))


def test_validator_rejects_data_depending_on_chunks_directly() -> None:
    with pytest.raises(PlanValidationError, match="rag.query chunks"):
        _validator().validate(_plot_plan("{{2}}", ["1", "2"], "numbers"))


def test_validator_rejects_mixed_placeholder_in_data() -> None:
    with pytest.raises(PlanValidationError, match="mixes a placeholder"):
        _validator().validate(_plot_plan("data: {{2}}", ["2"], "numbers"))


def test_validator_rejects_literal_data_with_dependency() -> None:
    with pytest.raises(PlanValidationError, match="no"):
        _validator().validate(
            _plot_plan('{"labels": ["a"], "values": [1]}', ["2"], "numbers")
        )
