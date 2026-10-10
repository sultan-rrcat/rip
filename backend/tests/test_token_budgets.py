"""Per-step token budgets: chat cheap, code generous (ADR-036)."""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.agents.base import DelegationRequest
from app.agents.coding import CodingAgent
from app.agents.reasoning import ReasoningAgent
from app.agents.registry import get_default_agent_registry
from app.core.config import settings
from app.orchestration.builders import build, build_chat
from app.orchestration.intents import Intent
from app.orchestration.router import RouterResult
from app.orchestration.validator import PlanValidator
from app.providers.base import ModelProvider
from app.tools.registry import get_default_tool_registry


class _RecordingProvider(ModelProvider):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None, cancel_event=None):
        self.calls.append({"max_tokens": max_tokens})
        return "ok"

    def generate_stream(self, model, messages, *, temperature=0.2, max_tokens=None, cancel_event=None):
        self.calls.append({"max_tokens": max_tokens})
        yield "ok"

    def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None, max_tokens=None):
        return {}

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake"}]


def _validator(provider) -> PlanValidator:
    return PlanValidator(
        get_default_agent_registry(provider), get_default_tool_registry()
    )


def test_chat_builder_caps_budget_low() -> None:
    plan = build_chat("hello")
    assert plan.steps[0].input["max_tokens"] == settings.chat_max_tokens
    assert settings.chat_max_tokens < settings.default_max_tokens
    _validator(_RecordingProvider()).validate(plan)


def test_code_builder_budget_generous(tmp_path, monkeypatch) -> None:
    # No code files and no hint -> greenfield generation step with
    # the generous budget (ADR-038), not a clarification.
    route = RouterResult(intent=Intent.CODE, confidence=0.9, file_hint="")
    plan = build("write a quicksort function", route, "(no documents)", None)
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].agent_id == "coding"
    assert (plan.steps[0].expected_output_type or "").lower() == "answer"
    assert plan.steps[0].input["max_tokens"] == settings.coding_max_tokens
    _validator(_RecordingProvider()).validate(plan)

    # Named-but-missing file -> clarification step, no explicit budget.
    missing = RouterResult(intent=Intent.CODE, confidence=0.9, file_hint="nosuchfile")
    plan = build("review nosuchfile", missing, "(no documents)", None)
    assert plan is not None and len(plan.steps) == 1
    assert (plan.steps[0].expected_output_type or "").lower() == "clarification"
    assert "max_tokens" not in plan.steps[0].input
    _validator(_RecordingProvider()).validate(plan)

    # With a code file on disk -> file inlined, generous budget set.
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    nb = "nb1"
    (tmp_path / nb).mkdir()
    fid = "11111111-1111-1111-1111-111111111111"
    (tmp_path / nb / (fid + ".py")).write_text("def add(a, b):\n    return a + b\n")
    snap = f"1 file(s): app.py [ready:code] id={fid}"
    plan = build("write a quicksort function", route, snap, nb)
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].input["max_tokens"] == settings.coding_max_tokens
    assert settings.coding_max_tokens > settings.default_max_tokens
    _validator(_RecordingProvider()).validate(plan)

    # Execute verbs ("write a test" names testing) route to the sandbox
    # DAG (ADR-050) — and its coding report step keeps the generous
    # budget so stdout + changed files are never cut by the token cap.
    plan = build("write a test", route, snap, nb)
    assert plan is not None and len(plan.steps) == 2
    assert plan.steps[0].tool_id == "code.sandbox"
    assert plan.steps[1].agent_id == "coding"
    assert plan.steps[1].input["max_tokens"] == settings.coding_max_tokens
    _validator(_RecordingProvider()).validate(plan)


def test_code_review_budget_uses_shared_default(tmp_path, monkeypatch) -> None:
    # Review/explain is input-heavy but output-short: cap at the shared
    # default so "review the python code" cannot burn the 300s wall-clock.
    from app.orchestration.builders import _coding_budget_for

    assert _coding_budget_for("review the python code") == settings.default_max_tokens
    assert _coding_budget_for("explain validator.py") == settings.default_max_tokens
    assert _coding_budget_for("write a test") == settings.coding_max_tokens
    assert settings.coding_max_tokens == 4096

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    nb = "nb1"
    (tmp_path / nb).mkdir()
    fid = "11111111-1111-1111-1111-111111111111"
    (tmp_path / nb / (fid + ".py")).write_text("x = 1\n")
    snap = f"1 file(s): app.py [ready:code] id={fid}"
    route = RouterResult(intent=Intent.CODE, confidence=0.9, file_hint="")
    plan = build("review the python code", route, snap, nb)
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].input["max_tokens"] == settings.default_max_tokens


def test_code_vague_ask_with_many_files_clarifies() -> None:
    route = RouterResult(intent=Intent.CODE, confidence=0.9, file_hint="")
    snap = (
        "2 file(s): a.py [ready:code] id=11111111-1111-1111-1111-111111111111; "
        "b.py [ready:code] id=22222222-2222-2222-2222-222222222222"
    )
    plan = build("review the python code", route, snap, "nb1")
    assert plan is not None and len(plan.steps) == 1
    assert (plan.steps[0].expected_output_type or "").lower() == "clarification"


def test_coding_agent_default_is_coding_budget() -> None:
    provider = _RecordingProvider()
    resp = CodingAgent(provider).execute(
        DelegationRequest(step_id="1", trace_id="t", input={"message": "hi"})
    )
    assert resp.output == "ok"
    assert provider.calls[0]["max_tokens"] == settings.coding_max_tokens


def test_reasoning_agent_default_is_shared_default() -> None:
    provider = _RecordingProvider()
    ReasoningAgent(provider).execute(
        DelegationRequest(step_id="1", trace_id="t", input={"message": "hi"})
    )
    assert provider.calls[0]["max_tokens"] == settings.default_max_tokens


def test_per_step_override_wins_over_agent_default() -> None:
    provider = _RecordingProvider()
    CodingAgent(provider).execute(
        DelegationRequest(
            step_id="1", trace_id="t", input={"message": "hi"}, max_tokens=123
        )
    )
    assert provider.calls[0]["max_tokens"] == 123


def test_delegation_request_budget_defaults_none() -> None:
    req = DelegationRequest(step_id="1", trace_id="t", input={})
    assert req.max_tokens is None


def test_empty_model_output_fails_honest() -> None:
    # Live trace: coding step streamed 148s of nothing, reported SUCCESS
    # with output "" — the user saw a blank bubble. Empty output must be
    # FAILURE (retried by the engine, then surfaced honestly).
    from app.agents.base import StepStatus

    class _EmptyProvider(_RecordingProvider):
        def generate(self, model, messages, *, temperature=0.2, max_tokens=None, cancel_event=None):
            self.calls.append({"max_tokens": max_tokens})
            return "   \n  "

    for agent_cls in (CodingAgent, ReasoningAgent):
        resp = agent_cls(_EmptyProvider()).execute(
            DelegationRequest(step_id="1", trace_id="t", input={"message": "hi"})
        )
        assert resp.status is StepStatus.FAILURE
        assert resp.output is None
        assert resp.error


class _StructuredBudgetProvider(ModelProvider):
    """Records generate_structured kwargs; returns router/planner shapes."""

    def __init__(self) -> None:
        self.structured_calls: list[dict] = []

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None,
                 cancel_event=None):
        return "ok"

    def generate_stream(self, model, messages, *, temperature=0.2,
                        max_tokens=None, cancel_event=None):
        yield "ok"

    def generate_structured(self, model, messages, schema, *, temperature=0.0,
                            timeout_ms=None, cancel_event=None,
                            max_tokens=None):
        self.structured_calls.append({"max_tokens": max_tokens})
        return {"intent": "chat", "queries": ["q"], "quotes": []}

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake"}]


def test_router_structured_budget_is_tight() -> None:
    # One enum word of JSON must not inherit the 2048 shared default:
    # thinking burns the same num_predict budget as the answer.
    from app.orchestration.router import ROUTER_MAX_TOKENS, Router

    assert ROUTER_MAX_TOKENS == 256
    provider = _StructuredBudgetProvider()
    Router(provider).route("hello there friend, how are you doing today?")
    assert provider.structured_calls
    assert all(
        c["max_tokens"] == ROUTER_MAX_TOKENS for c in provider.structured_calls
    )


def test_rag_subquery_planner_budget_is_tight() -> None:
    from app.tools.base import ToolRequest
    from app.tools.rag_query import _SUBQUERY_MAX_TOKENS, RagQueryTool

    assert _SUBQUERY_MAX_TOKENS == 256

    class _Rag:
        def retrieve_context(self, notebook_id, query, top_k=4,
                             file_id=None, file_name=None, mode="specific"):
            return {"results": []}

    provider = _StructuredBudgetProvider()
    resp = RagQueryTool(rag=_Rag(), provider=provider).execute(
        ToolRequest(
            tool_id="rag.query", step_id="1", trace_id="t",
            input={"notebook_id": "nb-1", "query": "q"},
        )
    )
    assert resp.ok
    assert provider.structured_calls
    assert all(
        c["max_tokens"] == _SUBQUERY_MAX_TOKENS
        for c in provider.structured_calls
    )
