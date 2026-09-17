"""Phase 2 — deterministic builder tests (no Ollama, no DB)."""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.agents.registry import AgentRegistry
from app.orchestration.builders import build, build_compare_multi
from app.orchestration.intents import Intent
from app.orchestration.router import RouterResult
from app.orchestration.validator import PlanValidator
from app.providers.base import ModelProvider
from app.tools.registry import get_default_tool_registry


class _FakeProvider(ModelProvider):
    def generate(self, model, messages, *, temperature=0.2, max_tokens=None):
        return "ok"

    def generate_structured(self, model, messages, schema, *, temperature=0.0):
        return {"goal": "g", "steps": []}

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake"}]


def _validator() -> PlanValidator:
    provider = _FakeProvider()
    agents = AgentRegistry()
    from app.agents.reasoning import ReasoningAgent

    agents.register(ReasoningAgent(provider))
    return PlanValidator(agents, get_default_tool_registry())


def test_compare_multi_structure_and_placeholders() -> None:
    plan = build_compare_multi(
        ["fire report sections", "faultbook report sections"],
        "compare both report and rank them based on complexity",
    )
    assert len(plan.steps) == 3
    assert [s.tool_id for s in plan.steps[:2]] == ["rag.query", "rag.query"]
    assert all(
        (s.expected_output_type or "").lower() == "chunks" for s in plan.steps[:2]
    )
    final = plan.steps[2]
    assert final.agent_id == "reasoning"
    assert final.depends_on == ["1", "2"]
    assert "{{1}}" in str(final.input) and "{{2}}" in str(final.input)
    _validator().validate(plan)  # must pass grounding checks


def test_compare_multi_pads_short_queries() -> None:
    plan = build_compare_multi([], "compare A and B")
    assert len(plan.steps) == 3
    _validator().validate(plan)


def test_build_dispatch() -> None:
    route = RouterResult(
        intent=Intent.COMPARE_MULTI,
        queries=["a", "b"],
        confidence=0.9,
        routed_by="llm",
    )
    plan = build("compare a and b", route)
    assert plan is not None and len(plan.steps) == 3

    chat = build("hello", RouterResult(intent=Intent.CHAT, confidence=1.0))
    assert chat is not None and len(chat.steps) == 1

    unknown = build("???", RouterResult(intent=Intent.UNKNOWN, confidence=0.0))
    assert unknown is None
