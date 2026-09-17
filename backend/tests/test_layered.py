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
    def retrieve_context(self, notebook_id, query, top_k=8):
        return {
            "query": query,
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
