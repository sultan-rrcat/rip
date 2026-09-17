"""Phase 3.2 — orchestration: Planner → Engine → Aggregator.

Fakes throughout (no Ollama, no DB): FakeProvider serves canned structured
plans, FakeAgents execute deterministically, FakeRAG stands in for VectorRAG
(real e2e awaits the Phase 6 torch repair). Run with
``pytest backend/tests/test_orchestration.py -q --noconftest`` until the
torch env is repaired — the shared conftest imports app.main, which needs
sentence_transformers.
"""

from __future__ import annotations

import threading

import pytest
from app.agents.base import (
    Agent,
    DelegationRequest,
    DelegationResponse,
    StepStatus,
)
from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.orchestration.aggregator import Aggregator
from app.orchestration.memory import (
    MemoryContext,
    build_memory_context,
    estimate_tokens,
)
from app.orchestration.orchestrator import (
    OrchestrationError,
    Orchestrator,
)
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.plan_graph import run_plan_graph
from app.orchestration.planner import Planner
from app.orchestration.results import ExecutionResult, StepResult
from app.orchestration.validator import PlanValidationError, PlanValidator
from app.providers.base import ModelProvider
from app.tools.base import Tool, ToolRequest, ToolResponse
from app.tools.registry import ToolRegistry, get_default_tool_registry


class FakeProvider(ModelProvider):
    """Canned ModelProvider; records the model every call resolves to."""

    def __init__(self, structured: dict | None = None, text: str = "ok",
                 queued: list[dict] | None = None):
        self.structured = structured or {"goal": "g", "steps": []}
        self.text = text
        self.models: list[str] = []
        # Sequential plans for recall tests; every structured prompt kept
        # so tests can assert on RETRY FEEDBACK content.
        self.queued = list(queued) if queued else None
        self.prompts: list = []

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None):
        self.models.append(model)
        return self.text

    def generate_stream(self, model, messages, *, temperature=0.2, max_tokens=None):
        self.models.append(model)
        yield self.text

    def generate_structured(self, model, messages, schema, *, temperature=0.0):
        self.models.append(model)
        self.prompts.append(messages)
        if self.queued:
            return dict(self.queued.pop(0))
        return dict(self.structured)

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake-model", "display_name": "fake-model"}]

    def generate_image(self, prompt: str):
        raise NotImplementedError("test fake")


class FakeAgent(Agent):
    """Deterministic agent; optionally streams one delta chunk."""

    agent_id = "fake"
    name = "Fake"
    description = "test agent"

    def __init__(self, output: str = "done", stream: bool = False,
                 clarify: bool = False):
        self.output = output
        self.stream = stream
        self.clarify = clarify
        self.seen: list[dict] = []

    def execute(self, request: DelegationRequest) -> DelegationResponse:
        self.seen.append(dict(request.input))
        if self.stream and request.on_delta is not None:
            request.on_delta("chunk-")
        return DelegationResponse(
            step_id=request.step_id, status=StepStatus.SUCCESS, output=self.output,
            needs_clarification=self.clarify,
        )


class FakeTool(Tool):
    """Deterministic recording tool for input-scoping assertions."""

    tool_id = "fake.tool"
    name = "FakeTool"
    description = "test tool"

    def __init__(self):
        self.seen: list[dict] = []

    def execute(self, request: ToolRequest) -> ToolResponse:
        self.seen.append(dict(request.input))
        return ToolResponse(tool_id=self.tool_id, ok=True, output="tool-out")


class FakeRAG:
    def __init__(self):
        self.seen: list[tuple] = []

    def retrieve_context(self, notebook_id, query, top_k=8):
        self.seen.append((notebook_id, query, top_k))
        return {
            "query": query,
            "results": [
                {
                    "content": "chunk-one",
                    "source": "f.pdf",
                    "section": "H1",
                    "rerank_score": 0.9,
                }
            ],
        }


def _registries(agent: Agent, rag=None) -> tuple[AgentRegistry, ToolRegistry]:
    agents = AgentRegistry()
    agents.register(agent)
    return agents, get_default_tool_registry(rag=rag)


def _ok(step_id: str, output: str, executor: str = "reasoning") -> StepResult:
    return StepResult(
        step_id=step_id, agent_id=executor, status=StepStatus.SUCCESS, output=output
    )


def _fail(step_id: str, error: str, executor: str = "reasoning") -> StepResult:
    return StepResult(
        step_id=step_id, agent_id=executor, status=StepStatus.FAILURE, error=error
    )


# --- Validator ---


class TestValidator:
    def test_valid_plan_passes(self):
        from unittest.mock import MagicMock

        from app.agents.registry import get_default_agent_registry

        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(
                    step_id="1", agent_id="reasoning",
                    input={"message": "hi"},
                )
            ],
        )
        agents = get_default_agent_registry(MagicMock())
        tools = get_default_tool_registry(rag=FakeRAG())
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_unknown_agent_rejected(self):
        agents, tools = _registries(FakeAgent())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[PlanStep(step_id="1", agent_id="nope", input={})],
        )
        with pytest.raises(PlanValidationError):
            PlanValidator(agents, tools).validate(plan)

    def test_both_or_neither_rejected(self):
        agents, tools = _registries(FakeAgent())
        both = Plan(
            plan_id="p", goal="g",
            steps=[PlanStep(step_id="1", agent_id="fake", tool_id="rag.query")],
        )
        with pytest.raises(PlanValidationError):
            PlanValidator(agents, tools).validate(both)

    def test_cycle_rejected(self):
        agents, tools = _registries(FakeAgent())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="fake", input={}, depends_on=["2"]),
                PlanStep(step_id="2", agent_id="fake", input={}, depends_on=["1"]),
            ],
        )
        with pytest.raises(PlanValidationError, match="cycle"):
            PlanValidator(agents, tools).validate(plan)

    def test_budget_rejected(self):
        agents, tools = _registries(FakeAgent())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id=str(i), agent_id="fake", input={})
                for i in range(3)
            ],
        )
        with pytest.raises(PlanValidationError):
            PlanValidator(agents, tools, max_steps=2).validate(plan)

    def test_dependent_plot_without_placeholder_rejected(self):
        # Run 4efaec2b shape: dependent plot, hardcoded literals, no {{id}}.
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", agent_id="fake", input={"message": "summarize {{1}}"},
                         depends_on=["1"], expected_output_type="summary"),
                PlanStep(step_id="3", tool_id="plot.chart",
                         input={"chart_type": "bar", "labels": ["Class 0", "Class 1"],
                                "values": [50, 50]},
                         depends_on=["2"], expected_output_type="chart"),
            ],
        )
        with pytest.raises(PlanValidationError, match="placeholder"):
            PlanValidator(agents, tools).validate(plan)

    def test_plot_referencing_non_numbers_rejected(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", tool_id="plot.chart",
                         input={"chart_type": "bar", "labels": ["a"], "values": ["{{1}}"]},
                         depends_on=["1"], expected_output_type="chart"),
            ],
        )
        with pytest.raises(PlanValidationError, match="numbers-producing"):
            PlanValidator(agents, tools).validate(plan)

    def test_garbled_placeholder_in_values_rejected(self):
        # Run 66fd4ec3 shape: text glued around placeholders.
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="fake", input={"message": "numbers"},
                         expected_output_type="numbers"),
                PlanStep(step_id="2", tool_id="plot.chart",
                         input={"chart_type": "bar", "labels": ["a", "b"],
                                "values": [",{{1}}", ",{{1}}"]},
                         depends_on=["1"], expected_output_type="chart"),
            ],
        )
        with pytest.raises(PlanValidationError, match="lone"):
            PlanValidator(agents, tools).validate(plan)

    def test_multi_source_plot_values_rejected(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="fake", input={"message": "n1"},
                         expected_output_type="numbers"),
                PlanStep(step_id="2", agent_id="fake", input={"message": "n2"},
                         expected_output_type="numbers"),
                PlanStep(step_id="3", tool_id="plot.chart",
                         input={"chart_type": "bar", "labels": ["a", "b"],
                                "values": ["{{1}}", "{{2}}"]},
                         depends_on=["1", "2"], expected_output_type="chart"),
            ],
        )
        with pytest.raises(PlanValidationError, match="merging numbers step"):
            PlanValidator(agents, tools).validate(plan)

    def test_mixed_literal_and_single_placeholder_passes(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="fake", input={"message": "n"},
                         expected_output_type="numbers"),
                PlanStep(step_id="2", tool_id="plot.chart",
                         input={"chart_type": "bar", "labels": ["a", "b"],
                                "values": [0.5, "{{1}}"]},
                         depends_on=["1"], expected_output_type="chart"),
            ],
        )
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_standalone_plot_with_literals_passes(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="plot.chart",
                         input={"chart_type": "bar", "labels": ["a", "b"], "values": [1, 2]},
                         expected_output_type="chart"),
            ],
        )
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_grounded_plot_pattern_passes(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", agent_id="fake", input={"message": "numbers {{1}}"},
                         depends_on=["1"], expected_output_type="numbers"),
                PlanStep(step_id="3", agent_id="fake", input={"message": "summarize {{1}}"},
                         depends_on=["1"], expected_output_type="answer"),
                PlanStep(step_id="4", tool_id="plot.chart",
                         input={"chart_type": "bar", "labels": ["Fire", "Smoke"],
                                "values": ["{{2}}"]},
                         depends_on=["2"], expected_output_type="chart"),
            ],
        )
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_ungrounded_doc_generate_rejected(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", tool_id="doc.generate",
                         input={"title": "t", "sections": []},
                         depends_on=["1"], expected_output_type="document"),
            ],
        )
        with pytest.raises(PlanValidationError, match="answer/summary/text"):
            PlanValidator(agents, tools).validate(plan)

    def test_ungrounded_reasoning_rejected(self):
        # Run 4ad8adfc shape: reasoning depends on rag.query but message
        # carries no {{1}} — executes ungrounded, asks to re-upload.
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", agent_id="fake",
                         input={"message": "write 5 beginner MCQs"},
                         depends_on=["1"], expected_output_type="answer"),
            ],
        )
        with pytest.raises(PlanValidationError, match="placeholder"):
            PlanValidator(agents, tools).validate(plan)

    def test_grounded_reasoning_passes(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", agent_id="fake",
                         input={"message": "write 15 MCQs from {{1}}"},
                         depends_on=["1"], expected_output_type="answer"),
            ],
        )
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_doc_generate_without_title_rejected(self):
        # Run 4ad8adfc attempt-1 shape: doc.generate with message, no title.
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="doc.generate",
                         input={"message": "write MCQs"},
                         expected_output_type="document"),
            ],
        )
        with pytest.raises(PlanValidationError, match="title"):
            PlanValidator(agents, tools).validate(plan)

    def test_rag_without_query_rejected(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={},
                         expected_output_type="chunks"),
            ],
        )
        with pytest.raises(PlanValidationError, match="query"):
            PlanValidator(agents, tools).validate(plan)

    def test_rag_query_must_be_typed_chunks(self):
        # Raw chunks typed as text would leak into the answer bubble.
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="text"),
            ],
        )
        with pytest.raises(PlanValidationError, match="chunks"):
            PlanValidator(agents, tools).validate(plan)


# --- Aggregator (Q36 deterministic rules) ---


class TestAggregator:
    def test_empty_is_failed(self):
        agg = Aggregator().aggregate(
            Plan(plan_id="p", goal="g"),
            ExecutionResult(trace_id="t", step_results=[]),
        )
        assert agg.status == "failed" and agg.plan_incomplete

    def test_single_success_verbatim(self):
        agg = Aggregator().aggregate(
            Plan(plan_id="p", goal="g"),
            ExecutionResult(trace_id="t", step_results=[_ok("1", "the answer")]),
        )
        assert (agg.status, agg.summary) == ("success", "the answer")

    def test_multiple_success_labeled_join(self):
        agg = Aggregator().aggregate(
            Plan(plan_id="p", goal="g"),
            ExecutionResult(
                trace_id="t",
                step_results=[_ok("1", "aaa"), _ok("2", "bbb", executor="coding")],
            ),
        )
        assert agg.status == "success"
        assert agg.summary == (
            "Step 1 (reasoning): aaa\n\nStep 2 (coding): bbb"
        )

    def test_partial_status(self):
        agg = Aggregator().aggregate(
            Plan(plan_id="p", goal="g"),
            ExecutionResult(
                trace_id="t", step_results=[_ok("1", "aaa"), _fail("2", "boom")]
            ),
        )
        assert agg.status == "partial"
        assert agg.summary.startswith("aaa")
        assert "Step 2 (reasoning) failed: boom" in agg.summary

    def test_all_failed_joins_errors(self):
        agg = Aggregator().aggregate(
            Plan(plan_id="p", goal="g"),
            ExecutionResult(trace_id="t", step_results=[_fail("1", "boom")]),
        )
        assert agg.status == "failed"
        assert "Step 1 (reasoning) failed: boom" in agg.summary

    def test_clarification_verbatim(self):
        agg = Aggregator().aggregate(
            Plan(plan_id="p", goal="g"),
            ExecutionResult(
                trace_id="t",
                step_results=[
                    StepResult(
                        step_id="1", agent_id="reasoning",
                        status=StepStatus.SUCCESS, output="Which file?",
                        needs_clarification=True,
                    )
                ],
            ),
        )
        assert agg.summary == "Which file?" and agg.needs_clarification

    def test_answer_plus_chart_shows_text_and_placeholder(self):
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", agent_id="reasoning", input={},
                         depends_on=["1"], expected_output_type="answer"),
                PlanStep(step_id="3", tool_id="plot.chart", input={},
                         depends_on=["2"], expected_output_type="chart"),
            ],
        )
        agg = Aggregator().aggregate(
            plan,
            ExecutionResult(
                trace_id="t",
                step_results=[
                    _ok("1", "raw chunks", executor="rag.query"),
                    _ok("2", "Fire 62pct, Smoke 38pct"),
                    StepResult(
                        step_id="3", agent_id="plot.chart",
                        status=StepStatus.SUCCESS,
                        output="<svg>chart</svg>",
                    ),
                ],
            ),
        )
        assert agg.status == "success"
        assert "Fire 62pct" in agg.summary
        assert "Chart generated" in agg.summary
        assert "raw chunks" not in agg.summary

    def test_numbers_plus_chart_hides_numbers(self):
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="reasoning", input={},
                         expected_output_type="numbers"),
                PlanStep(step_id="2", tool_id="plot.chart", input={},
                         depends_on=["1"], expected_output_type="chart"),
            ],
        )
        agg = Aggregator().aggregate(
            plan,
            ExecutionResult(
                trace_id="t",
                step_results=[
                    _ok("1", "88518, 52770"),
                    StepResult(
                        step_id="2", agent_id="plot.chart",
                        status=StepStatus.SUCCESS,
                        output="<SVG>chart</SVG>",
                    ),
                ],
            ),
        )
        assert agg.summary == "Chart generated — see Artifacts below."

    def test_all_hidden_plus_failure_shows_failures_only(self):
        # Run 66fd4ec3 shape: hidden numbers + failed plot must not dump
        # the raw numbers CSV as the visible answer.
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="reasoning", input={},
                         expected_output_type="numbers"),
                PlanStep(step_id="2", tool_id="plot.chart", input={},
                         depends_on=["1"], expected_output_type="chart"),
            ],
        )
        agg = Aggregator().aggregate(
            plan,
            ExecutionResult(
                trace_id="t",
                step_results=[
                    _ok("1", "0.88, 0.87"),
                    _fail("2", "'values' must all be numbers", executor="plot.chart"),
                ],
            ),
        )
        assert agg.status == "partial"
        assert "0.88" not in agg.summary
        assert "Step 2 (plot.chart) failed" in agg.summary

    def test_answer_plus_failed_chart_keeps_answer(self):
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="reasoning", input={},
                         expected_output_type="answer"),
                PlanStep(step_id="2", tool_id="plot.chart", input={},
                         depends_on=["1"], expected_output_type="chart"),
            ],
        )
        agg = Aggregator().aggregate(
            plan,
            ExecutionResult(
                trace_id="t",
                step_results=[
                    _ok("1", "the summary"),
                    _fail("2", "bad values", executor="plot.chart"),
                ],
            ),
        )
        assert agg.status == "partial"
        assert "the summary" in agg.summary
        assert "Step 2 (plot.chart) failed: bad values" in agg.summary


# --- Memory (Q28) ---


class TestMemory:
    def test_no_fold_within_window(self):
        provider = FakeProvider()
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        ctx, summary, count = build_memory_context(provider, None, msgs)
        assert provider.models == []  # no LLM call
        assert summary is None and count == 0 and len(ctx.recent) == 5

    def test_fold_uses_ollama_default_model(self):
        provider = FakeProvider(text="rolled")
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(12)]
        ctx, summary, count = build_memory_context(provider, None, msgs)
        assert summary == "rolled" and count == 2
        assert provider.models == [settings.ollama_default_model]
        assert "rolled" in ctx.as_prompt()

    def test_fold_dedups_by_count(self):
        provider = FakeProvider(text="rolled")
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(12)]
        _, summary, count = build_memory_context(provider, "old", msgs, folded_count=2)
        assert summary == "old" and count == 2 and provider.models == []

    def test_token_budget_trims_oldest(self):
        ctx, _, _ = build_memory_context(
            FakeProvider(), None,
            [{"role": "user", "content": "x" * 400} for _ in range(4)],
            max_tokens=150,  # 4x100T -> trims to the single newest 100T turn
        )
        assert ctx.truncated and len(ctx.recent) == 1

    def test_estimate_tokens(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcdefgh") == 2
        assert isinstance(MemoryContext().as_prompt(), str)


# --- Plan graph ---


class TestPlanGraph:
    def test_rag_query_gets_notebook_id(self):
        rag = FakeRAG()
        agents, tools = _registries(FakeAgent(), rag=rag)
        plan = Plan(
            plan_id="p", goal="answer",
            steps=[
                PlanStep(
                    step_id="1", tool_id="rag.query",
                    input={"query": "hello"},  # no notebook_id from planner
                )
            ],
        )
        result = run_plan_graph(
            plan, agents, tool_registry=tools, trace_id="t", notebook_id="nb-7"
        )
        assert result.step_results[0].status is StepStatus.SUCCESS
        assert rag.seen and rag.seen[0][0] == "nb-7"  # injected, not LLM-made
        assert "chunk-one" in (result.step_results[0].output or "")
        assert result.step_results[0].data["sources"] == [
            {"source": "f.pdf", "section": "H1"}
        ]

    def test_agent_steps_do_not_get_notebook_id(self):
        agent = FakeAgent()
        agents, tools = _registries(agent, rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[PlanStep(step_id="1", agent_id="fake", input={"message": "hi"})],
        )
        run_plan_graph(
            plan, agents, tool_registry=tools, trace_id="t", notebook_id="nb-1"
        )
        assert "notebook_id" not in agent.seen[0]

    def test_placeholders_resolve_across_steps(self):
        agents, tools = _registries(FakeAgent(output="21"), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="fake", input={"message": "count"}),
                PlanStep(
                    step_id="2", tool_id="plot.chart",
                    input={
                        "chart_type": "bar", "labels": ["x"],
                        "values": ["{{1}}"], "title": "n={{1}}",
                    },
                    depends_on=["1"],
                ),
            ],
        )
        result = run_plan_graph(plan, agents, tool_registry=tools, trace_id="t")
        assert result.step_results[1].status is StepStatus.SUCCESS
        assert result.step_results[1].data["point_count"] == 1

    def test_cancel_short_circuits(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[PlanStep(step_id="1", agent_id="fake", input={})],
        )
        event = threading.Event()
        event.set()
        result = run_plan_graph(
            plan, agents, tool_registry=tools, trace_id="t", cancel_event=event
        )
        assert result.step_results[0].error == "run cancelled"

    def test_context_scoped_to_terminal_answer(self):
        agent = FakeAgent(output="done")
        agents, tools = _registries(agent, rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", agent_id="fake", input={"message": "numbers"},
                         expected_output_type="numbers"),
                PlanStep(step_id="2", agent_id="fake", input={"message": "answer {{1}}"},
                         depends_on=["1"], expected_output_type="answer"),
            ],
        )
        result = run_plan_graph(
            plan, agents, tool_registry=tools, trace_id="t",
            context="CTX", fallback_message="FB",
        )
        assert result.step_results[0].status is StepStatus.SUCCESS
        assert "context" not in agent.seen[0]  # intermediate: task + upstream only
        assert agent.seen[1].get("context") == "CTX"  # terminal prose: full context

    def test_tool_steps_get_no_context_or_fallback(self):
        tool = FakeTool()
        tools = ToolRegistry()
        tools.register(tool)
        agents, _ = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="fake.tool",
                         input={"query": "x", "context": "stale", "history": []}),
            ],
        )
        result = run_plan_graph(
            plan, agents, tool_registry=tools, trace_id="t", notebook_id="nb-1",
            context="CTX", fallback_message="FB",
        )
        assert result.step_results[0].status is StepStatus.SUCCESS
        seen = tool.seen[0]
        assert "context" not in seen and "history" not in seen
        assert "message" not in seen  # no fallback prose for tools
        assert seen.get("notebook_id") == "nb-1"  # run truth still injected

    def test_trivial_plan_empty(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        result = run_plan_graph(
            Plan(plan_id="p", goal="g", steps=[]), agents,
            tool_registry=tools, trace_id="t",
        )
        assert result.step_results == []


# --- Planner ---


class TestPlanner:
    def test_plan_uses_ollama_default_model(self):
        provider = FakeProvider(
            structured={"goal": "answer things", "steps": []}
        )
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Planner(provider, agents, tools).plan("hello")
        assert plan.goal == "answer things" and plan.steps == []
        assert provider.models == [settings.ollama_default_model]

    def test_feedback_absent_by_default(self):
        provider = FakeProvider(structured={"goal": "g", "steps": []})
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        Planner(provider, agents, tools).plan("hello")
        system = provider.prompts[-1][0]["content"]
        assert "RETRY FEEDBACK" not in system

    def test_feedback_appended_when_given(self):
        provider = FakeProvider(structured={"goal": "g", "steps": []})
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        Planner(provider, agents, tools).plan("hello", feedback="fix the values")
        system = provider.prompts[-1][0]["content"]
        assert "RETRY FEEDBACK" in system and "fix the values" in system


# --- Bounded planner recall (one retry = two plans max) ---


def _recall_orchestrator(plans: list[dict], agent=None, rag=None):
    provider = FakeProvider(queued=plans)
    agents = AgentRegistry()
    agents.register(agent or FakeAgent(output="recovered"))
    tools = get_default_tool_registry(rag=rag or FakeRAG())
    orch = Orchestrator(
        Planner(provider, agents, tools),
        PlanValidator(agents, tools),
        Aggregator(), agents, tools,
    )
    return provider, orch


def _good_text_step():
    return {
        "goal": "g",
        "steps": [
            {"step_id": "1", "agent_id": "fake", "input": {"message": "hi"},
             "depends_on": [], "expected_output_type": "text"}
        ],
    }


def _bad_plot_step():
    return {
        "goal": "g",
        "steps": [
            {"step_id": "1", "agent_id": "fake", "input": {"message": "n"},
             "depends_on": [], "expected_output_type": "numbers"},
            {"step_id": "2", "tool_id": "plot.chart",
             "input": {"chart_type": "bar", "labels": ["a"], "values": [50]},
             "depends_on": ["1"], "expected_output_type": "chart"},
        ],
    }


class TestPlannerRecall:
    def test_validation_retry_recovers(self):
        provider, orch = _recall_orchestrator([_bad_plot_step(), _good_text_step()])
        result = orch.run("plot it", "nb-1")
        assert result.status == "success" and result.summary == "recovered"
        assert len(provider.models) == 2
        retry_system = provider.prompts[-1][0]["content"]
        assert "RETRY FEEDBACK" in retry_system and "hardcoded" in retry_system

    def test_double_validation_failure_aborts_honest(self):
        provider, orch = _recall_orchestrator([_bad_plot_step(), _bad_plot_step()])
        with pytest.raises(OrchestrationError, match="hardcoded"):
            orch.run("plot it", "nb-1")
        assert len(provider.models) == 2

    def test_clarification_never_replans(self):
        provider, orch = _recall_orchestrator(
            [{
                "goal": "g",
                "steps": [
                    {"step_id": "1", "agent_id": "fake",
                     "input": {"message": "Which file?"},
                     "depends_on": [], "expected_output_type": "clarification"}
                ],
            }],
            agent=FakeAgent(output="Which file?", clarify=True),
        )
        result = orch.run("convert it", "nb-1")
        assert result.summary == "Which file?" and result.needs_clarification
        assert len(provider.models) == 1

    def test_partial_execution_replans_once(self):
        provider, orch = _recall_orchestrator([
            {
                "goal": "g",
                "steps": [
                    {"step_id": "1", "tool_id": "plot.chart",
                     "input": {"chart_type": "nope", "labels": ["a"], "values": [1]},
                     "depends_on": []}
                ],
            },
            _good_text_step(),
        ])
        result = orch.run("plot it", "nb-1")
        assert result.status == "success" and result.summary == "recovered"
        assert len(provider.models) == 2
        retry_system = provider.prompts[-1][0]["content"]
        assert "RETRY FEEDBACK" in retry_system

    def test_cancel_suppresses_recall(self):
        provider, orch = _recall_orchestrator([_bad_plot_step(), _good_text_step()])
        event = threading.Event()
        event.set()
        with pytest.raises(OrchestrationError, match="cancelled"):
            orch.run("plot it", "nb-1", cancel_event=event)
        assert len(provider.models) == 0


# --- Full Orchestrator e2e ---


class TestOrchestrator:
    def _orchestrator(self, structured: dict, rag=None, stream=False):
        provider = FakeProvider(structured=structured)
        agent = FakeAgent(output="final answer", stream=stream)
        agents = AgentRegistry()
        agents.register(agent)
        tools = get_default_tool_registry(rag=rag or FakeRAG())
        planner = Planner(provider, agents, tools)
        validator = PlanValidator(agents, tools)
        return Orchestrator(planner, validator, Aggregator(), agents, tools)

    def test_rag_plan_end_to_end(self):
        orch = self._orchestrator(
            {
                "goal": "answer from docs",
                "steps": [
                    {
                        "step_id": "1", "tool_id": "rag.query",
                        "input": {"query": "hello"},
                        "depends_on": [], "expected_output_type": "chunks",
                    },
                    {
                        "step_id": "2", "agent_id": "fake",
                        "input": {"message": "answer it using {{1}}"},
                        "depends_on": ["1"], "expected_output_type": "answer",
                    },
                ],
            }
        )
        events: list[dict] = []
        result = orch.run(
            "what do docs say?", "nb-e2e",
            on_event=events.append, context="prior chat",
        )
        assert result.status == "success"
        # Type-aware aggregation (ADR-023 as amended): intermediate chunks
        # are hidden, so the terminal answer surfaces verbatim.
        assert result.summary == "final answer"
        assert "Step 1 (rag.query)" not in (result.summary or "")
        assert result.goal == "answer from docs"
        types = [e["type"] for e in events]
        assert "step_started" in types and "step_completed" in types

    def test_streaming_delta_events(self):
        orch = self._orchestrator(
            {
                "goal": "g",
                "steps": [
                    {
                        "step_id": "1", "agent_id": "fake",
                        "input": {"message": "hi"},
                        "depends_on": [], "expected_output_type": "text",
                    }
                ],
            },
            stream=True,
        )
        events: list[dict] = []
        orch.run("hi", "nb-1", on_event=events.append)
        deltas = [e for e in events if e["type"] == "delta"]
        assert deltas and deltas[0]["step_id"] == "1"

    def test_trivial_plan_falls_back_to_single_step(self):
        orch = self._orchestrator({"goal": "nothing", "steps": []})
        result = orch.run("hi", "nb-1")
        assert result.status == "success" and not result.plan_incomplete
        assert len(result.step_results) == 1
        assert result.summary == "final answer"

    def test_plan_error_raises(self):
        orch = self._orchestrator(
            {
                "goal": "g",
                "steps": [
                    {
                        "step_id": "1", "agent_id": "ghost",
                        "input": {}, "depends_on": [],
                    }
                ],
            }
        )
        with pytest.raises(OrchestrationError):
            orch.run("hi", "nb-1")

    def test_cancelled_run_raises(self):
        orch = self._orchestrator({"goal": "g", "steps": []})
        event = threading.Event()
        event.set()
        with pytest.raises(OrchestrationError, match="cancelled"):
            orch.run("hi", "nb-1", cancel_event=event)

    def test_signature_has_notebook_id_and_context(self):
        import inspect

        params = list(inspect.signature(Orchestrator.run).parameters)
        assert params[1:5] == [
            "request_text", "notebook_id", "on_event", "context",
        ]

    def test_execution_failure_recalls_once_then_fails_honest(self):
        # Bounded recall supersedes the old no-replan lock: a failing step
        # triggers exactly ONE replan; the repeat failure surfaces honestly.
        provider = FakeProvider(
            structured={
                "goal": "g",
                "steps": [
                    {
                        "step_id": "1", "tool_id": "plot.chart",
                        "input": {"chart_type": "nope", "labels": ["a"], "values": [1]},
                        "depends_on": [],
                    }
                ],
            }
        )
        plans = []
        orig = provider.generate_structured

        def counting(*a, **k):
            plans.append(1)
            return orig(*a, **k)

        provider.generate_structured = counting  # type: ignore[method-assign]
        agents = AgentRegistry()
        agents.register(FakeAgent())
        tools = get_default_tool_registry(rag=FakeRAG())
        orch = Orchestrator(
            Planner(provider, agents, tools),
            PlanValidator(agents, tools),
            Aggregator(), agents, tools,
        )
        result = orch.run("plot it", "nb-1")
        assert plans == [1, 1]
        assert result.status == "failed" and result.plan_incomplete


# --- Nested executor ids (live trace: model buries agent_id/tool_id in input) ---


class TestNestedExecutorHoist:
    def test_from_model_hoists_tool_id(self):
        plan = Plan.from_model(
            "p", "g",
            [{"step_id": "1", "input": {"tool_id": "rag.query", "query": "x"}}],
        )
        assert plan.steps[0].tool_id == "rag.query"
        assert "tool_id" not in plan.steps[0].input
        assert plan.steps[0].input["query"] == "x"

    def test_from_model_hoists_agent_id(self):
        plan = Plan.from_model(
            "p", "g",
            [{"step_id": "1", "input": {"agent_id": "fake", "message": "hi"}}],
        )
        assert plan.steps[0].agent_id == "fake"
        assert "agent_id" not in plan.steps[0].input
        assert plan.steps[0].input["message"] == "hi"

    def test_from_model_leaves_top_level_set(self):
        plan = Plan.from_model(
            "p", "g",
            [{"step_id": "1", "agent_id": "fake",
              "input": {"message": "hi", "tool_id": "rag.query"}}],
        )
        assert plan.steps[0].agent_id == "fake"
        assert plan.steps[0].tool_id is None
        assert plan.steps[0].input["tool_id"] == "rag.query"

    def test_from_model_leaves_both_nested_for_validator(self):
        plan = Plan.from_model(
            "p", "g",
            [{"step_id": "1",
              "input": {"agent_id": "fake", "tool_id": "rag.query"}}],
        )
        assert plan.steps[0].agent_id == "" and plan.steps[0].tool_id is None
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        with pytest.raises(PlanValidationError, match="TOP-LEVEL"):
            PlanValidator(agents, tools).validate(plan)

    def test_validator_hint_names_top_level(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[PlanStep(step_id="1", input={"tool_id": "rag.query"})],
        )
        with pytest.raises(PlanValidationError, match="TOP-LEVEL"):
            PlanValidator(agents, tools).validate(plan)

    def test_validator_without_nesting_has_no_hint(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g", steps=[PlanStep(step_id="1", input={})]
        )
        with pytest.raises(PlanValidationError) as exc:
            PlanValidator(agents, tools).validate(plan)
        assert "TOP-LEVEL" not in str(exc.value)

    def test_e2e_nested_ids_execute(self):
        provider = FakeProvider(
            structured={
                "goal": "g",
                "steps": [
                    {
                        "step_id": "1",
                        "input": {"tool_id": "rag.query", "query": "hello"},
                        "depends_on": [], "expected_output_type": "chunks",
                    },
                    {
                        "step_id": "2",
                        "input": {"agent_id": "fake", "message": "answer {{1}}"},
                        "depends_on": ["1"], "expected_output_type": "answer",
                    },
                ],
            }
        )
        agents = AgentRegistry()
        agents.register(FakeAgent(output="final"))
        tools = get_default_tool_registry(rag=FakeRAG())
        orch = Orchestrator(
            Planner(provider, agents, tools),
            PlanValidator(agents, tools),
            Aggregator(), agents, tools,
        )
        result = orch.run("hi", "nb-1")
        assert result.status == "success" and result.summary == "final"


# --- Structural repair (live traces 77930808/26e4974f: stray elements,
# nested step objects, omitted eot) ---


class TestStructuralRepair:
    def test_non_dict_element_rejected(self):
        with pytest.raises(ValueError, match="not an object"):
            Plan.from_model(
                "p", "g",
                [
                    {"step_id": "1", "tool_id": "rag.query",
                     "input": {"query": "x"},
                     "expected_output_type": "chunks"},
                    {"step_id": "2", "agent_id": "fake",
                     "input": {"message": "answer {{1}}"},
                     "depends_on": ["1"], "expected_output_type": "answer"},
                    "step_id",
                ],
            )

    def test_nested_step_in_input_rejected(self):
        with pytest.raises(ValueError, match="buries step"):
            Plan.from_model(
                "p", "g",
                [
                    {"step_id": "1", "tool_id": "rag.query",
                     "input": {"query": "x"},
                     "expected_output_type": "chunks"},
                    {"step_id": "3",
                     "input": {"message": "write MCQs {{1}}", "step_id": "2",
                               "depends_on": ["1"],
                               "expected_output_type": "answer"},
                     "depends_on": ["1"], "expected_output_type": "text"},
                ],
            )

    def test_omitted_rag_eot_defaults_chunks(self):
        plan = Plan.from_model(
            "p", "Create 15 MCQs",
            [
                {"step_id": "1", "tool_id": "rag.query",
                 "input": {"query": "x", "top_k": 8}},
                {"step_id": "2", "agent_id": "fake",
                 "input": {"message": "write 15 MCQs from {{1}}"},
                 "expected_output_type": "answer"},
            ],
        )
        assert plan.steps[0].expected_output_type == "chunks"
        assert plan.steps[1].depends_on == ["1"]
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_validator_names_buried_step(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[PlanStep(step_id="3", input={"message": "x", "step_id": "2"})],
        )
        with pytest.raises(PlanValidationError, match="buries step"):
            PlanValidator(agents, tools).validate(plan)

    def test_plan_skeleton_flags_shapes(self):
        from app.orchestration.engine import _plan_skeleton

        dump = (
            '{"goal": "g", "steps": ['
            '{"step_id": "1", "tool_id": "rag.query", '
            '"input": {"query": "x"}, "depends_on": []}, '
            '"step_id"]}'
        )
        skeleton = _plan_skeleton(dump)
        assert "executor=rag.query" in skeleton
        assert "NOT AN OBJECT" in skeleton


# --- Wiring-key hoist (live trace 214e7509: ornith-1.5:9b nests
# depends_on/expected_output_type inside input, twice running) ---


class TestKeyHoist:
    def _rag_then_writer(self, writer_input):
        return [
            {"step_id": "1", "tool_id": "rag.query",
             "input": {"query": "x", "top_k": 8}},
            {"step_id": "2", "agent_id": "fake", "input": writer_input},
        ]

    def test_attempt1_shape_hoisted(self):
        # depends_on + expected_output_type buried in input move up.
        plan = Plan.from_model(
            "p", "Summarize the document",
            self._rag_then_writer({
                "message": "Summarize using ONLY these chunks: {{1}}",
                "depends_on": ["1"], "expected_output_type": "answer",
            }),
        )
        assert plan.steps[0].expected_output_type == "chunks"
        assert plan.steps[1].depends_on == ["1"]
        assert plan.steps[1].expected_output_type == "answer"
        assert "depends_on" not in plan.steps[1].input
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_attempt2_shape_hoisted(self):
        # expected_output_type "summary" buried in input moves up and
        # validates (summary is a known answer type).
        plan = Plan.from_model(
            "p", "Summarize the document",
            self._rag_then_writer({
                "message": "Using ONLY {{1}}, write a summary.",
                "expected_output_type": "summary",
            }),
        )
        assert plan.steps[1].depends_on == ["1"]  # auto-wired from {{1}}
        assert plan.steps[1].expected_output_type == "summary"
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_contradiction_rejected(self):
        with pytest.raises(ValueError, match="contradicts"):
            Plan.from_model(
                "p", "g",
                [
                    {"step_id": "1", "tool_id": "rag.query",
                     "input": {"query": "x"},
                     "expected_output_type": "chunks"},
                    {"step_id": "2", "agent_id": "fake",
                     "input": {"message": "x {{1}}",
                               "expected_output_type": "summary"},
                     "depends_on": ["1"], "expected_output_type": "answer"},
                ],
            )

    def test_depends_on_contradiction_rejected(self):
        with pytest.raises(ValueError, match="contradicts"):
            Plan.from_model(
                "p", "g",
                [
                    {"step_id": "1", "tool_id": "rag.query",
                     "input": {"query": "x"},
                     "expected_output_type": "chunks"},
                    {"step_id": "2", "tool_id": "rag.query",
                     "input": {"query": "y", "depends_on": ["9"]},
                     "depends_on": ["1"],
                     "expected_output_type": "chunks"},
                ],
            )

    def test_echo_dropped(self):
        # input step_id equal to the top-level id is a harmless echo.
        plan = Plan.from_model(
            "p", "g",
            [
                {"step_id": "1", "tool_id": "rag.query",
                 "input": {"query": "x", "step_id": "1"},
                 "expected_output_type": "chunks"},
            ],
        )
        assert "step_id" not in plan.steps[0].input

    def test_whole_step_nesting_still_rejected(self):
        # Differing input step_id with no top-level executor: the old
        # buried-step shape stays a retry-actionable ValueError.
        with pytest.raises(ValueError, match="buries step"):
            Plan.from_model(
                "p", "g",
                [{
                    "input": {"message": "x", "step_id": "2",
                              "depends_on": ["1"],
                              "expected_output_type": "answer"},
                    "depends_on": [], "expected_output_type": "text",
                }],
            )


# --- Placeholder edges (live trace be49925d: grounded message, missing edge) ---


class TestPlaceholderEdges:
    def test_from_model_autowires_missing_edge(self):
        # Planner emits {{1}} but omits depends_on — same super-step means
        # the placeholder never resolves (literal "{{1}}" reaches the LLM).
        plan = Plan.from_model(
            "p", "Create 15 MCQs",
            [
                {"step_id": "1", "tool_id": "rag.query",
                 "input": {"query": "x", "top_k": 8},
                 "expected_output_type": "chunks"},
                {"step_id": "2", "agent_id": "fake",
                 "input": {"message": "write 15 MCQs from {{1}}"},
                 "expected_output_type": "answer"},
            ],
        )
        assert plan.steps[1].depends_on == ["1"]
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        assert PlanValidator(agents, tools).validate(plan) is plan

    def test_dangling_placeholder_rejected(self):
        agents, tools = _registries(FakeAgent(), rag=FakeRAG())
        plan = Plan(
            plan_id="p", goal="g",
            steps=[
                PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"},
                         expected_output_type="chunks"),
                PlanStep(step_id="2", agent_id="fake",
                         input={"message": "use {{1}} and {{99}}"},
                         depends_on=["1"], expected_output_type="answer"),
            ],
        )
        with pytest.raises(PlanValidationError, match="unknown step"):
            PlanValidator(agents, tools).validate(plan)

    def test_e2e_autowired_mcq_shape_resolves_chunks(self):
        # End-to-end: planner omits the edge; from_model wires it so the
        # writer sees resolved chunks instead of literal "{{1}}".
        provider = FakeProvider(
            structured={
                "goal": "Create 15 MCQs",
                "steps": [
                    {"step_id": "1", "tool_id": "rag.query",
                     "input": {"query": "hello"},
                     "depends_on": [], "expected_output_type": "chunks"},
                    {"step_id": "2", "agent_id": "fake",
                     "input": {"message": "write MCQs from {{1}}"},
                     "expected_output_type": "answer"},
                ],
            }
        )
        agent = FakeAgent(output="final")
        agents = AgentRegistry()
        agents.register(agent)
        tools = get_default_tool_registry(rag=FakeRAG())
        orch = Orchestrator(
            Planner(provider, agents, tools),
            PlanValidator(agents, tools),
            Aggregator(), agents, tools,
        )
        result = orch.run("hi", "nb-1")
        assert result.status == "success" and result.summary == "final"
        assert "{{1}}" not in agent.seen[0]["message"]
        assert "chunk-one" in agent.seen[0]["message"]
