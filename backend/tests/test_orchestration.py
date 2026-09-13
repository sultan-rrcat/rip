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
from app.tools.registry import ToolRegistry, get_default_tool_registry


class FakeProvider(ModelProvider):
    """Canned ModelProvider; records the model every call resolves to."""

    def __init__(self, structured: dict | None = None, text: str = "ok"):
        self.structured = structured or {"goal": "g", "steps": []}
        self.text = text
        self.models: list[str] = []

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None):
        self.models.append(model)
        return self.text

    def generate_stream(self, model, messages, *, temperature=0.2, max_tokens=None):
        self.models.append(model)
        yield self.text

    def generate_structured(self, model, messages, schema, *, temperature=0.0):
        self.models.append(model)
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

    def __init__(self, output: str = "done", stream: bool = False):
        self.output = output
        self.stream = stream
        self.seen: list[dict] = []

    def execute(self, request: DelegationRequest) -> DelegationResponse:
        self.seen.append(dict(request.input))
        if self.stream and request.on_delta is not None:
            request.on_delta("chunk-")
        return DelegationResponse(
            step_id=request.step_id, status=StepStatus.SUCCESS, output=self.output
        )


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
        assert agg.status == "partial" and agg.summary == "aaa"

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
                        "input": {"message": "answer it"},
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
        # Q36: two successes → labeled concatenation, not the bare answer.
        assert "Step 2 (fake): final answer" in (result.summary or "")
        assert "Step 1 (rag.query)" in (result.summary or "")
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

    def test_trivial_plan_failed_result(self):
        orch = self._orchestrator({"goal": "nothing", "steps": []})
        result = orch.run("hi", "nb-1")
        assert result.status == "failed" and result.plan_incomplete

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

    def test_graph_has_no_reflection_edge(self):
        # Honest failure: a failing step must NOT trigger a replan —
        # the planner is called exactly once.
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
        assert plans == [1]
        assert result.status == "failed" and result.plan_incomplete
