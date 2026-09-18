"""Layered planning chain tests: L2 builder hit, L3 mega fallback, L0 fast-path.

Uses the real default registries (reasoning agent + rag.query tool) with a
fake provider/RAG — no Ollama, no DB.
"""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.agents.registry import get_default_agent_registry
from app.orchestration.aggregator import Aggregator
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.planner import Planner
from app.orchestration.validator import PlanValidationError, PlanValidator
from app.providers.base import ModelProvider
from app.tools.registry import get_default_tool_registry


class FakeRAG:
    def retrieve_context(
        self, notebook_id, query, top_k=8, file_id=None, file_name=None, mode="specific"
    ):
        return {
            "query": query,
            "mode": mode,
            "results": [
                {
                    "content": f"chunk for {query}",
                    "source": "f.pdf",
                    "section": "H1",
                    "rerank_score": 0.9,
                }
            ],
        }


class FakeLayeredProvider(ModelProvider):
    def __init__(self, text="layered answer", queued=None):
        self.text = text
        self.queued = list(queued) if queued else []
        self.models: list[str] = []
        self.prompts: list = []
        self.structured_calls = 0

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None):
        self.models.append(model)
        return self.text

    def generate_stream(self, model, messages, *, temperature=0.2, max_tokens=None):
        self.models.append(model)
        yield self.text

    def generate_structured(self, model, messages, schema, *, temperature=0.0):
        self.models.append(model)
        self.prompts.append(messages)
        self.structured_calls += 1
        return dict(self.queued.pop(0))

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake"}]


def _orchestrator(provider):
    agents = get_default_agent_registry(provider)
    tools = get_default_tool_registry(rag=FakeRAG())
    return Orchestrator(
        Planner(provider, agents, tools),
        PlanValidator(agents, tools),
        Aggregator(), agents, tools,
    )


COMPARE_REQUEST = "compare both report and rank them based on complexity"


def test_compare_request_uses_builder_plan_without_mega_call() -> None:
    provider = FakeLayeredProvider(queued=[
        {
            "intent": "compare_multi",
            "queries": ["fire report sections", "faultbook sections"],
            "confidence": 0.9,
        },
    ])
    orch = _orchestrator(provider)
    result = orch.run(COMPARE_REQUEST, "nb-layered")
    assert result.status == "success"
    assert result.summary == "layered answer"
    assert len(result.step_results) == 3  # 2×rag.query + reasoning fan-in
    assert result.goal == COMPARE_REQUEST
    # Only the router structured call ran — the mega-prompt planner was
    # never asked (its queued payload would raise IndexError if consumed).
    assert provider.structured_calls == 1
    assert "intent router" in provider.prompts[0][0]["content"]
    assert result.shown == ["3"] and result.hidden == ["1", "2"]


def test_unknown_intent_falls_back_to_mega_prompt() -> None:
    mega = {
        "goal": "mega goal",
        "steps": [
            {"step_id": "1", "agent_id": "reasoning",
             "input": {"message": "mega answer"},
             "depends_on": [], "expected_output_type": "text"}
        ],
    }
    provider = FakeLayeredProvider(
        queued=[{"intent": "unknown", "queries": [], "confidence": 0.0}, mega]
    )
    result = _orchestrator(provider).run(
        "a vague long request with no clear shape at all here", "nb-1"
    )
    assert result.status == "success" and result.goal == "mega goal"
    assert provider.structured_calls == 2  # router + planner


def test_greeting_fast_path_spends_no_structured_call() -> None:
    provider = FakeLayeredProvider()
    result = _orchestrator(provider).run("hi", "nb-1")
    assert result.status == "success" and result.summary == "layered answer"
    assert len(result.step_results) == 1
    assert provider.structured_calls == 0  # fast-path: no router, no planner


def _prose_validator():
    provider = FakeLayeredProvider()
    agents = get_default_agent_registry(provider)
    return PlanValidator(agents, get_default_tool_registry(rag=FakeRAG()))


def _rag_step(step_id: str) -> PlanStep:
    return PlanStep(
        step_id=step_id, tool_id="rag.query",
        input={"query": "x"}, expected_output_type="chunks",
    )


def test_prose_step_ref_without_placeholder_rejected() -> None:
    # Exact ecd93eb4 shape: prose names steps 1/2, no {{1}} {{2}}, no edges.
    import pytest

    plan = Plan(
        plan_id="p", goal="compare both reports",
        steps=[
            _rag_step("1"),
            _rag_step("2"),
            PlanStep(
                step_id="3", agent_id="reasoning",
                input={"message": (
                    "Using the retrieved chunks from step 1 (Fire report) "
                    "and step 2 (Faultbook report), compare and rank them."
                )},
                expected_output_type="answer",
            ),
        ],
    )
    with pytest.raises(PlanValidationError, match="placeholder"):
        _prose_validator().validate(plan)


def test_prose_step_ref_with_placeholders_passes() -> None:
    plan = Plan(
        plan_id="p", goal="compare both reports",
        steps=[
            _rag_step("1"),
            _rag_step("2"),
            PlanStep(
                step_id="3", agent_id="reasoning",
                input={"message": "Using {{1}} and {{2}}, compare step outputs."},
                depends_on=["1", "2"], expected_output_type="answer",
            ),
        ],
    )
    assert _prose_validator().validate(plan) is plan


def test_benign_step_prose_without_rag_siblings_passes() -> None:
    plan = Plan(
        plan_id="p", goal="g",
        steps=[
            PlanStep(
                step_id="1", agent_id="reasoning",
                input={"message": "follow step 1 of the checklist below"},
                expected_output_type="text",
            )
        ],
    )
    assert _prose_validator().validate(plan) is plan


def _react_orchestrator(provider):
    agents = get_default_agent_registry(provider)
    tools = get_default_tool_registry(rag=FakeRAG())
    return agents, tools


def test_react_answers_after_tool_observation() -> None:
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        {"thought": "need docs", "executor": "rag.query",
         "input": {"query": "fire"}, "is_final": False},
        {"thought": "have chunks", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react final"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "what do docs say?", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert outcome.result.step_results[-1].status.value == "success"
    assert outcome.result.step_results[-1].output == "react final"
    assert provider.structured_calls == 2
    # No placeholder wiring is ever emitted by the loop.
    assert "{{" not in str([s.input for s in outcome.plan.steps])


def test_react_rejects_unknown_executor_then_recovers() -> None:
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        {"thought": "bad pick", "executor": "ghost",
         "input": {}, "is_final": False},
        {"thought": "answer directly", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "recovered"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "answer this", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert outcome.result.step_results[-1].output == "recovered"
    assert provider.structured_calls == 2


def test_react_normalizes_nested_agent_input() -> None:
    from app.orchestration.react import (
        _normalize_react_input,
        _validate_react_input,
    )

    # Trace c9e59039 iter-1 shape: tool input wrapped under "agent".
    out = _normalize_react_input(
        "rag.query", {"agent": {"message": "fire distribution"}}
    )
    assert out["query"] == "fire distribution"
    assert "agent" not in out
    assert _validate_react_input("rag.query", out) is None

    out = _normalize_react_input(
        "code.sandbox", {"agent": {"message": "print(1)"}}
    )
    assert out["code"] == "print(1)"
    assert _validate_react_input("code.sandbox", out) is None

    # Stray tool_id key dropped (trace iter-3 shape).
    out = _normalize_react_input(
        "doc.convert", {"tool_id": "doc.convert", "file_id": "f", "target_format": "md"}
    )
    assert "tool_id" not in out
    assert _validate_react_input("doc.convert", out) is None


def test_react_validation_hints_without_executing() -> None:
    from app.orchestration.react import _validate_react_input, run_react

    assert "query" in (_validate_react_input("rag.query", {}) or "")
    assert "code" in (_validate_react_input("code.sandbox", {}) or "")
    assert "target_format" in (_validate_react_input("doc.convert", {"file_id": "f"}) or "")
    assert "labels" in (
        _validate_react_input("plot.chart", {"chart_type": "bar"}) or ""
    )

    # Malformed first turn gets a retry hint WITHOUT burning a step;
    # normalized second turn executes and the loop finishes.
    provider = FakeLayeredProvider(queued=[
        {"thought": "bad shape", "executor": "rag.query",
         "input": {"agent": {"message": ""}}, "is_final": False},
        {"thought": "need docs", "executor": "rag.query",
         "input": {"query": "fire"}, "is_final": False},
        {"thought": "have chunks", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react final"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "what do docs say?", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert outcome.result.step_results[-1].output == "react final"
    # Only ONE executed tool step (r2) + final answer: the malformed r1
    # never reached execution.
    assert [s.step_id for s in outcome.plan.steps] == ["r2", "r3"]


def test_react_prompt_states_flat_shapes_and_plot_preference() -> None:
    from app.orchestration.react import run_react

    seen: list = []

    class _ProbeProvider(FakeLayeredProvider):
        def generate_structured(self, model, messages, schema, *, temperature=0.0):
            seen.append(messages)
            return {"thought": "done", "executor": "reasoning",
                    "input": {}, "is_final": True, "answer": "ok"}

    agents, tools = _react_orchestrator(_ProbeProvider())
    run_react("plot this", _ProbeProvider(), agents, tools,
              trace_id="t", notebook_id="nb-1")
    system = seen[0][0]["content"]
    assert '{"query": "..."' in system
    assert "never nested under 'agent'" in system
    assert "plot.chart" in system and "never code.sandbox for charting" in system


def test_orchestrator_falls_back_to_react_on_double_plan_failure() -> None:
    ghost = {
        "goal": "g",
        "steps": [{"step_id": "1", "agent_id": "ghost", "input": {}}],
    }
    provider = FakeLayeredProvider(queued=[
        {"intent": "unknown", "queries": [], "confidence": 0.0},
        ghost, ghost,
        {"thought": "need docs", "executor": "rag.query",
         "input": {"query": "x"}, "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react rescued"},
    ])
    assert _orchestrator(provider).run("a long failing request here", "nb-1").summary == "react rescued"
    assert provider.structured_calls == 5  # router + 2 planners + 2 react turns


# --- Option A observability: router sibling span, plan enrichment, react spans.
#
# No Langfuse server needed: `manual_span` is monkeypatched with a recorder,
# so these tests prove the wiring (names, explicit parenting, outputs)
# without any tracing backend.


class _SpanRecorder:
    """Stand-in for `manual_span`: records (name, trace_context, updates)."""

    def __init__(self):
        self.spans: list[dict] = []

    def __call__(self, name, *, as_type="span", input=None, output=None,
                 metadata=None, trace_context=None, **extra):
        rec: dict = {
            "name": name,
            "trace_context": trace_context,
            "input": input,
            "output": None,
            "updates": [],
        }
        self.spans.append(rec)
        obs_id = f"span-{len(self.spans)}"

        class _Obs:
            id = obs_id

            def update(self, **kw):
                rec["updates"].append(kw)
                if "output" in kw:
                    rec["output"] = kw["output"]

        class _Ctx:
            def __enter__(self):
                return _Obs()

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def by_name(self, name: str) -> list[dict]:
        return [s for s in self.spans if s["name"] == name]


def _compare_provider() -> FakeLayeredProvider:
    return FakeLayeredProvider(queued=[
        {
            "intent": "compare_multi",
            "queries": ["fire report sections", "faultbook sections"],
            "confidence": 0.9,
        },
    ])


def test_plan_event_carries_routing_fields() -> None:
    events: list[dict] = []
    result = _orchestrator(_compare_provider()).run(
        COMPARE_REQUEST, "nb-1", on_event=events.append,
    )
    assert result.status == "success"
    plans = [e for e in events if e.get("type") == "plan"]
    assert len(plans) == 1
    route = plans[0].get("route") or {}
    assert route.get("intent") == "compare_multi"
    assert route.get("routed_by") == "llm"
    assert route.get("confidence") == 0.9


def test_greeting_plan_event_fast_path_route() -> None:
    events: list[dict] = []
    result = _orchestrator(FakeLayeredProvider()).run(
        "hi", "nb-1", on_event=events.append,
    )
    assert result.status == "success"
    plans = [e for e in events if e.get("type") == "plan"]
    assert len(plans) == 1
    route = plans[0].get("route") or {}
    assert route.get("routed_by") == "fast_path"
    assert route.get("intent") == "chat"


def test_router_span_is_sibling_of_plan_under_run(monkeypatch) -> None:
    from app.orchestration import engine

    recorder = _SpanRecorder()
    monkeypatch.setattr(engine, "manual_span", recorder)
    # Distinct well-formed sentinel for the plan-span context: proves the
    # router span does NOT parent under `plan` (it must carry the run ctx
    # instead). Well-formed (dict with trace_id/parent_span_id) so the
    # real step spans downstream still parent correctly when tracing is on.
    _plan_ctx = {"trace_id": "T", "parent_span_id": "PLAN-SPAN"}
    monkeypatch.setattr(engine, "get_trace_context", lambda: dict(_plan_ctx))
    result = _orchestrator(_compare_provider()).run(COMPARE_REQUEST, "nb-1")
    assert result.status == "success"
    routers = recorder.by_name("router")
    plans = recorder.by_name("plan")
    assert len(routers) == 1 and len(plans) == 1
    # Sibling parenting: both spans carry the run ctx from config, never the
    # plan-span ctx captured later via get_trace_context().
    assert routers[0]["trace_context"] == plans[0]["trace_context"]
    assert routers[0]["trace_context"] != _plan_ctx
    assert routers[0]["output"]["intent"] == "compare_multi"
    assert routers[0]["output"]["routed_by"] == "llm"
    assert plans[0]["output"]["layer"] == "L2-builder"
    assert plans[0]["output"]["intent"] == "compare_multi"


def test_plan_span_output_carries_layer_on_mega_fallback(monkeypatch) -> None:
    from app.orchestration import engine

    recorder = _SpanRecorder()
    monkeypatch.setattr(engine, "manual_span", recorder)
    mega = {
        "goal": "mega goal",
        "steps": [
            {"step_id": "1", "agent_id": "reasoning",
             "input": {"message": "mega answer"},
             "depends_on": [], "expected_output_type": "text"}
        ],
    }
    provider = FakeLayeredProvider(
        queued=[{"intent": "unknown", "queries": [], "confidence": 0.0}, mega]
    )
    result = _orchestrator(provider).run(
        "a vague long request with no clear shape at all here", "nb-1"
    )
    assert result.status == "success"
    plans = recorder.by_name("plan")
    assert len(plans) == 1
    assert plans[0]["output"]["layer"] == "L3-mega"
    assert plans[0]["output"]["intent"] == "unknown"


def test_react_iteration_spans(monkeypatch) -> None:
    import app.orchestration.react as react_mod
    from app.orchestration.react import run_react

    recorder = _SpanRecorder()
    monkeypatch.setattr(react_mod, "_manual_span", recorder)
    provider = FakeLayeredProvider(queued=[
        {"thought": "need docs", "executor": "rag.query",
         "input": {"query": "fire"}, "is_final": False},
        {"thought": "have chunks", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react final"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "what do docs say?", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert outcome.result.step_results[-1].output == "react final"
    iters = [s for s in recorder.spans if s["name"].startswith("react:iter-")]
    assert [s["name"] for s in iters] == ["react:iter-1", "react:iter-2"]
    assert iters[0]["output"]["status"] == "success"
    assert iters[0]["output"]["executor"] == "rag.query"
    assert iters[1]["output"]["is_final"] is True
    # Both iterations share the same explicit parent (the react ctx).
    assert iters[0]["trace_context"] == iters[1]["trace_context"]


def test_orchestrator_react_span(monkeypatch) -> None:
    import app.observability.langfuse as lf
    import app.orchestration.react as react_mod

    recorder = _SpanRecorder()
    # Orchestrator imports manual_span lazily (picks up the lf patch);
    # react binds _manual_span at module import, so patch both with the
    # same recorder. engine/plan_graph keep the real no-op here.
    monkeypatch.setattr(lf, "manual_span", recorder)
    monkeypatch.setattr(react_mod, "_manual_span", recorder)
    ghost = {
        "goal": "g",
        "steps": [{"step_id": "1", "agent_id": "ghost", "input": {}}],
    }
    provider = FakeLayeredProvider(queued=[
        {"intent": "unknown", "queries": [], "confidence": 0.0},
        ghost, ghost,
        {"thought": "need docs", "executor": "rag.query",
         "input": {"query": "x"}, "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react rescued"},
    ])
    assert _orchestrator(provider).run("a long failing request here", "nb-1").summary == "react rescued"
    reacts = recorder.by_name("react")
    assert len(reacts) == 1
    assert reacts[0]["output"]["status"] == "success"
    assert reacts[0]["output"]["steps"] == 2
    iters = [s for s in recorder.spans if s["name"].startswith("react:iter-")]
    assert len(iters) == 2


def test_qa_empty_notebook_skips_rag_query() -> None:
    # Trace ea48cb30: "What is QLoRA?" with "(no documents)" must build a
    # single general-answer reasoning step — no rag.query execution, only
    # the router structured call.
    provider = FakeLayeredProvider(queued=[
        {"intent": "qa_single", "queries": [], "confidence": 0.95},
    ])
    result = _orchestrator(provider).run(
        "What is QLoRA?", "nb-empty", notebook_context="(no documents)",
    )
    assert result.status == "success"
    assert result.summary == "layered answer"
    assert len(result.step_results) == 1
    assert result.step_results[0].agent_id == "reasoning"
    assert provider.structured_calls == 1


def test_compare_empty_notebook_yields_clarification() -> None:
    provider = FakeLayeredProvider(queued=[
        {"intent": "compare_multi", "queries": [], "confidence": 0.9},
    ])
    result = _orchestrator(provider).run(
        "compare both reports in this empty notebook please",
        "nb-empty",
        notebook_context="(no documents)",
    )
    assert result.status == "success"
    assert len(result.step_results) == 1
    assert result.shown == ["1"] and result.hidden == []
    assert provider.structured_calls == 1
