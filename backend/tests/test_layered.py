"""Layered planning chain tests: L2 builder hit, L3 ReAct fallback.

L1 router (sole dispatcher, every request via LLM) → L2 deterministic
builders → L3 ReAct. Uses the real default registries (reasoning agent +
rag.query tool) with a fake provider/RAG — no Ollama, no DB.
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
        self, notebook_id, query, top_k=4, file_id=None, file_name=None, mode="specific"
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

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None, cancel_event=None):
        self.models.append(model)
        return self.text

    def generate_stream(self, model, messages, *, temperature=0.2, max_tokens=None, cancel_event=None):
        self.models.append(model)
        yield self.text

    def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None):
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


def test_unknown_intent_falls_back_to_react() -> None:
    provider = FakeLayeredProvider(queued=[
        {"intent": "unknown", "queries": [], "confidence": 0.0},
        {"thought": "answer directly", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react answer"},
    ])
    result = _orchestrator(provider).run(
        "a vague long request with no clear shape at all here", "nb-1"
    )
    assert result.status == "success" and result.summary == "react answer"
    assert provider.structured_calls == 2  # router + react turn


def test_greeting_goes_through_router_to_builder() -> None:
    provider = FakeLayeredProvider(queued=[
        {"intent": "chat", "queries": [], "confidence": 0.95},
    ])
    result = _orchestrator(provider).run("hi", "nb-1")
    assert result.status == "success" and result.summary == "layered answer"
    assert len(result.step_results) == 1
    assert provider.structured_calls == 1  # L1 router, then L2 builder (no LLM)


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
        "rag.query", {"agent": {"message": "fire stats"}, "tool_id": "rag.query"}
    )
    assert out["query"] == "fire stats"
    assert "agent" not in out
    assert _validate_react_input("rag.query", out) is None

    # Stray tool_id key dropped (trace iter-3 shape).
    out = _normalize_react_input(
        "doc.convert", {"tool_id": "doc.convert", "file_id": "f", "target_format": "md"}
    )
    assert "tool_id" not in out
    assert _validate_react_input("doc.convert", out) is None


def test_react_validation_hints_without_executing() -> None:
    from app.orchestration.react import _validate_react_input, run_react

    assert "query" in (_validate_react_input("rag.query", {}) or "")
    assert _validate_react_input("notebook.inspect", {}) is None
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


def test_react_recovers_final_answer_stranded_in_input() -> None:
    # Trace 35e8fbd9 iter-1: is_final=true, answer empty, the full answer
    # rode inside input.content of a malformed doc.generate call. The loop
    # must recover it instead of discarding a correct answer.
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        {"thought": "format as table", "executor": "doc.generate",
         "input": {"title": "T", "content": "| A | B |\n|---|---|"},
         "is_final": True},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "in a table format", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert outcome.result.step_results[-1].status.value == "success"
    assert outcome.result.step_results[-1].output == "| A | B |\n|---|---|"
    assert provider.structured_calls == 1


def test_react_empty_final_answer_retries_with_actionable_hint() -> None:
    # Two consecutive is_final=true turns with no answer anywhere: the
    # first gets an actionable correction (not the old "retry." non-hint),
    # the second fails the loop honestly.
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True},
        {"thought": "still done", "executor": "reasoning",
         "input": {}, "is_final": True},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "answer this", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert outcome.result.step_results[-1].status.value == "failure"
    # Trace c1bbae95 replaced the internal "react loop produced no steps"
    # with an actionable message naming the real cause.
    error = outcome.result.step_results[-1].error or ""
    assert "react loop produced no steps" not in error
    assert "without producing an answer" in error
    assert "Try naming the file explicitly" in error
    assert provider.structured_calls == 2


def test_react_prompt_carries_all_tool_schemas() -> None:
    # Trace 35e8fbd9 iter-2: the model guessed doc.convert fields for
    # doc.generate twice — the prompt never showed doc.generate's schema.
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        {"thought": "answer", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "ok"},
    ])
    agents, tools = _react_orchestrator(provider)
    run_react("q", provider, agents, tools, trace_id="t", notebook_id="nb-1")
    system = provider.prompts[0][0]["content"]
    assert '"sections"' in system
    assert '"heading"' in system
    assert '"message"' in system  # agent message shape


def test_react_repeat_of_failed_executor_is_idle_turn() -> None:
    # Trace 27dcf635: code.sandbox ×2 (same docker error), rag.query ×2
    # (same empty result) — the second identical proposal must not execute.
    from app.orchestration.react import run_react
    from app.tools.base import Tool, ToolRequest, ToolResponse
    from app.tools.registry import ToolRegistry

    class _FailTool(Tool):
        tool_id = "fail.tool"
        name = "Fail"
        description = "always fails"
        effect_class = "read-only"  # type: ignore[assignment]

        def __init__(self):
            self.calls = 0

        def execute(self, request: ToolRequest) -> ToolResponse:
            self.calls += 1
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error="boom"
            )

    provider = FakeLayeredProvider(queued=[
        {"thought": "try it", "executor": "fail.tool",
         "input": {}, "is_final": False},
        {"thought": "try it again", "executor": "fail.tool",
         "input": {}, "is_final": False},
        {"thought": "give up", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "failed over"},
    ])
    agents, _ = _react_orchestrator(provider)
    fail_tool = _FailTool()
    tools = ToolRegistry()
    tools.register(fail_tool)
    outcome = run_react(
        "do the thing", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    # One proposal executes (plan_graph retries a failed step 3×: 1 + 2
    # retries); the identical repeat never executes (6 calls without it).
    assert fail_tool.calls == 3
    assert outcome.result.step_results[-1].output == "failed over"
    # r1 executed, r2 idle (no step), r3 final answer.
    assert [s.step_id for s in outcome.plan.steps] == ["r1", "r3"]
    assert provider.structured_calls == 3


def test_react_refuses_rag_query_on_empty_corpus() -> None:
    # Trace 27dcf635 iters 2+6: rag.query on "(no documents)" provably
    # returns "(no chunks retrieved)" — refuse it as an idle turn.
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        {"thought": "need data", "executor": "rag.query",
         "input": {"query": "gdp"}, "is_final": False},
        {"thought": "answer directly", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "no docs answer"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "plot gdp", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
        notebook_context="(no documents)",
    )
    assert outcome.result.step_results[-1].output == "no docs answer"
    assert [s.step_id for s in outcome.plan.steps] == ["r2"]
    assert provider.structured_calls == 2


def test_react_refuses_redundant_convert_after_generate() -> None:
    # Trace 126a2e57: doc.generate delivered the table as PDF (target
    # defaulted from the request text), then the next iteration
    # "converted" the ORIGINAL 18-page upload to pdf — a full copy of
    # the source alongside the report. Same-format converts of snapshot
    # uploads after a generate are refused as idle turns.
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        {"thought": "make report", "executor": "doc.generate",
         "input": {"title": "T",
                   "sections": [{"heading": "H", "body": "tab"}]},
         "is_final": False},
        {"thought": "convert it", "executor": "doc.convert",
         "input": {"file_id": "src-1", "target_format": "pdf"},
         "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "table pdf ready"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "I want the above table in a PDF document", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
        notebook_context="1 file(s): CD_lab_report.pdf [ready] id=src-1",
    )
    # r2 convert never executed: r1 generate + r3 final answer only.
    assert [s.step_id for s in outcome.plan.steps] == ["r1", "r3"]
    assert outcome.result.step_results[-1].output == "table pdf ready"
    assert provider.structured_calls == 3


def test_redundant_convert_hint_unit() -> None:
    from app.orchestration.react import _redundant_convert_hint

    gen = {"pdf"}
    src = {"src-1", "*"}
    assert _redundant_convert_hint(
        {"file_id": "src-1", "target_format": "pdf"}, gen, src
    ) is not None
    # Different format is legitimate work — still executes.
    assert _redundant_convert_hint(
        {"file_id": "src-1", "target_format": "docx"}, gen, src
    ) is None
    # Non-source id still executes (fails honestly inside the tool).
    assert _redundant_convert_hint(
        {"file_id": "made-up", "target_format": "pdf"}, gen, src
    ) is None
    # Nothing generated yet — converts flow normally.
    assert _redundant_convert_hint(
        {"file_id": "src-1", "target_format": "pdf"}, set(), src
    ) is None


HTML_CHART_REQUEST = (
    "Create a single self-contained HTML page that visualizes YOLO model "
    "comparison data as a chart. Use only HTML, CSS and JavaScript. Do not "
    "use external libraries or CDN links. Return only the complete HTML code."
)


def test_router_prompt_is_deliverable_first_for_code_output() -> None:
    # Trace c1bbae95: "self-contained HTML page ... as a chart ... no CDN ...
    # return only the HTML" was routed to plot_standalone at 0.99 confidence,
    # had no builder, and the run died asking for a re-upload. The router
    # prompt must classify by DELIVERABLE (source code) over chart wording.
    from app.orchestration.router import Router

    seen: list = []

    class _ProbeProvider(FakeLayeredProvider):
        def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None):
            seen.append(messages)
            return {"intent": "code", "confidence": 0.9}

    Router(_ProbeProvider()).route(HTML_CHART_REQUEST)
    system = seen[0][0]["content"]
    assert "DELIVERABLE-FIRST" in system
    assert "SOURCE CODE" in system
    assert "no CDN" in system


def test_misnamed_code_read_still_reaches_coding_agent() -> None:
    """End-to-end: the c1bbae95 loop shape must produce a real answer.

    The model filed its coding call under code.read (no input at all, then a
    full message with file_name=null) on a notebook with no files. That burned
    both idle turns and the run failed with zero steps. The remap must route
    it to the coding agent and answer.
    """
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(queued=[
        # iter 1: code.read with NO input at all (the original trace shape).
        {"thought": "I'll write the full HTML directly",
         "executor": "code.read", "is_final": False},
        # iter 2: code.read carrying the coding prompt (also original).
        {"thought": "still writing it",
         "executor": "code.read", "is_final": False,
         "input": {"message": HTML_CHART_REQUEST, "file_name": None}},
    ])
    provider.text = "<!DOCTYPE html><html><body>chart</body></html>"
    agents, tools = _react_orchestrator(provider)
    result = run_react(
        HTML_CHART_REQUEST, provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
        notebook_context="(no documents)",
    )
    statuses = [r.status.value for r in result.result.step_results]
    assert "success" in statuses, statuses
    outputs = " ".join(r.output or "" for r in result.result.step_results)
    assert "DOCTYPE html" in outputs
    # No step may be the dead-end no-steps placeholder.
    assert not any("no usable action" in (r.error or "") for r in result.result.step_results)


def test_react_prompt_allows_parametric_numbers_without_docs() -> None:
    from app.orchestration.react import run_react

    seen: list = []

    class _ProbeProvider(FakeLayeredProvider):
        def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None):
            seen.append(messages)
            return {"thought": "done", "executor": "reasoning",
                    "input": {}, "is_final": True, "answer": "ok"}

    agents, tools = _react_orchestrator(_ProbeProvider())
    run_react("plot gdp", _ProbeProvider(), agents, tools,
              trace_id="t", notebook_id="nb-1",
              notebook_context="(no documents)")
    system = seen[0][0]["content"]
    assert "recall approximate figures with a reasoning step first" in system


def test_react_prompt_states_flat_shapes_and_plot_preference() -> None:
    from app.orchestration.react import run_react

    seen: list = []

    class _ProbeProvider(FakeLayeredProvider):
        def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None):
            seen.append(messages)
            return {"thought": "done", "executor": "reasoning",
                    "input": {}, "is_final": True, "answer": "ok"}

    agents, tools = _react_orchestrator(_ProbeProvider())
    run_react("plot this", _ProbeProvider(), agents, tools,
              trace_id="t", notebook_id="nb-1")
    system = seen[0][0]["content"]
    assert '{"query": "..."' in system
    assert "never nested under 'agent'" in system
    assert "plot.chart" in system and "target_format" in system
    assert "title" in system


def test_orchestrator_falls_back_to_react_on_builder_miss() -> None:
    provider = FakeLayeredProvider(queued=[
        {"intent": "unknown", "queries": [], "confidence": 0.0},
        {"thought": "need docs", "executor": "rag.query",
         "input": {"query": "x"}, "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react rescued"},
    ])
    assert _orchestrator(provider).run("a long failing request here", "nb-1").summary == "react rescued"
    assert provider.structured_calls == 3  # router + 2 react turns


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


def test_greeting_plan_event_llm_route() -> None:
    events: list[dict] = []
    provider = FakeLayeredProvider(queued=[
        {"intent": "chat", "queries": [], "confidence": 0.95},
    ])
    result = _orchestrator(provider).run(
        "hi", "nb-1", on_event=events.append,
    )
    assert result.status == "success"
    plans = [e for e in events if e.get("type") == "plan"]
    assert len(plans) == 1
    route = plans[0].get("route") or {}
    assert route.get("routed_by") == "llm"
    assert route.get("intent") == "chat"


def test_router_span_is_sibling_of_plan_under_run(monkeypatch) -> None:
    from app.orchestration import engine, plan_graph

    recorder = _SpanRecorder()
    monkeypatch.setattr(engine, "manual_span", recorder)
    # Hermetic step spans: when a real Langfuse client is primed (full
    # suite via app.main lifespan), the sentinel plan-span ctx below would
    # otherwise reach the real plan_graph.manual_span and raise on the
    # invalid IDs. Step-span parenting is not under test here.
    monkeypatch.setattr(plan_graph, "manual_span", recorder)
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


def test_builder_miss_emits_no_plan_span_and_runs_react(monkeypatch) -> None:
    from app.orchestration import engine

    recorder = _SpanRecorder()
    monkeypatch.setattr(engine, "manual_span", recorder)
    provider = FakeLayeredProvider(queued=[
        {"intent": "unknown", "queries": [], "confidence": 0.0},
        {"thought": "answer directly", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "react answer"},
    ])
    result = _orchestrator(provider).run(
        "a vague long request with no clear shape at all here", "nb-1"
    )
    assert result.status == "success"
    # Builder miss delegates to L3 ReAct: no L2 plan span is emitted.
    assert recorder.by_name("plan") == []
    routers = recorder.by_name("router")
    assert len(routers) == 1
    assert routers[0]["output"]["intent"] == "unknown"


def test_react_iteration_spans(monkeypatch) -> None:
    import app.orchestration.react_engine as react_mod
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
    import app.orchestration.react_engine as react_mod

    recorder = _SpanRecorder()
    # Orchestrator imports manual_span lazily (picks up the lf patch);
    # react binds _manual_span at module import, so patch both with the
    # same recorder. engine/plan_graph keep the real no-op here.
    monkeypatch.setattr(lf, "manual_span", recorder)
    monkeypatch.setattr(react_mod, "_manual_span", recorder)
    provider = FakeLayeredProvider(queued=[
        {"intent": "unknown", "queries": [], "confidence": 0.0},
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


def test_react_broad_ask_defaults_rag_query_to_overview() -> None:
    # Trace cfbaa9c3: "summarize the docs" loop retrieved REFERENCES via
    # specific ranking instead of overview stratification.
    from app.orchestration.react import _default_react_mode, run_react

    assert _default_react_mode("summarize the docs", {"query": "x"})["mode"] == "overview"
    assert "mode" not in _default_react_mode("what is QLoRA?", {"query": "x"})
    assert _default_react_mode("summarize", {"query": "x", "mode": "specific"})["mode"] == "specific"

    provider = FakeLayeredProvider(
        text="synthesized",
        queued=[
            {"thought": "need docs", "executor": "rag.query",
             "input": {"query": "summarize"}, "is_final": False},
            {"thought": "have chunks", "executor": "reasoning",
             "input": {}, "is_final": True, "answer": "react final"},
        ],
    )
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "summarize the docs", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    rag_steps = [s for s in outcome.plan.steps if s.tool_id == "rag.query"]
    assert rag_steps and all(s.input.get("mode") == "overview" for s in rag_steps)


def test_react_synthesizes_answer_when_iterations_exhaust() -> None:
    # Trace cfbaa9c3 iter-6: no is_final, final summary was a truncated raw
    # chunk dump. The loop must synthesize prose from observations instead.
    from app.orchestration.aggregator import Aggregator
    from app.orchestration.react import run_react

    provider = FakeLayeredProvider(
        text="synthesized summary covering both docs",
        queued=[
            {"thought": "get more", "executor": "rag.query",
             "input": {"query": "summarize"}, "is_final": False},
            {"thought": "get even more", "executor": "rag.query",
             "input": {"query": "summarize again"}, "is_final": False},
        ],
    )
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "summarize the docs", provider, agents, tools,
        trace_id="t", notebook_id="nb-1", max_iterations=2,
    )
    assert outcome.result.step_results[-1].agent_id == "reasoning"
    assert outcome.result.step_results[-1].output == "synthesized summary covering both docs"
    agg = Aggregator().aggregate(outcome.plan, outcome.result)
    assert agg.summary == "synthesized summary covering both docs"
    assert agg.shown == ["r-final"]


def test_react_plot_nested_values_is_idle_hint() -> None:
    # Trace 07fb4f59 iters 1+4: nested `values` arrays (and an invented
    # `series_labels` key) burned executions on "'values' must all be
    # numbers". Malformed shapes must get a corrective hint WITHOUT
    # executing so the iteration budget survives for a fixed shape.
    from app.orchestration.react import _validate_react_input, run_react

    hint = _validate_react_input(
        "plot.chart",
        {"chart_type": "bar", "labels": ["Documents", "Pages"],
         "values": [[229, 135], [66, 47.5]]},
    )
    assert hint is not None and "flat" in hint and "series" in hint
    hint2 = _validate_react_input(
        "plot.chart",
        {"chart_type": "bar", "labels": ["A", "B"], "values": [1, 2],
         "series_labels": ["Documents"]},
    )
    assert hint2 is not None and "series_labels" in hint2

    provider = FakeLayeredProvider(queued=[
        {"thought": "bad shape", "executor": "plot.chart",
         "input": {"chart_type": "bar", "labels": ["Documents", "Pages"],
                   "values": [[229, 135], [66, 47.5]]}, "is_final": False},
        {"thought": "fixed shape", "executor": "plot.chart",
         "input": {"chart_type": "bar", "labels": ["DocBench", "MMLongBench"],
                   "values": [229, 135], "title": "docs"}, "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "have chart"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "compare docs as bar graph", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    # Malformed r1 never executed: only the fixed r2 chart + final answer.
    assert [s.step_id for s in outcome.plan.steps] == ["r2", "r3"]
    assert outcome.result.step_results[-1].output == "have chart"


def test_react_plot_without_title_is_idle_hint() -> None:
    # Trace affdbbd4: 3 of 4 charts rendered untitled — the model was
    # never asked for one. A title-less proposal must get a corrective
    # hint WITHOUT executing so the retry carries the same data + title.
    from app.orchestration.react import _validate_react_input, run_react

    hint = _validate_react_input(
        "plot.chart",
        {"chart_type": "bar", "labels": ["A", "B"], "values": [1, 2]},
    )
    assert hint is not None and "title" in hint
    hint_series = _validate_react_input(
        "plot.chart",
        {"chart_type": "bar", "labels": ["A", "B"],
         "series": [{"label": "s", "values": [1, 2]}]},
    )
    assert hint_series is not None and "title" in hint_series
    assert _validate_react_input(
        "plot.chart",
        {"chart_type": "bar", "labels": ["A", "B"], "values": [1, 2],
         "title": "t"},
    ) is None

    provider = FakeLayeredProvider(queued=[
        {"thought": "plot it", "executor": "plot.chart",
         "input": {"chart_type": "bar", "labels": ["A", "B"],
                   "values": [1, 2]}, "is_final": False},
        {"thought": "plot it titled", "executor": "plot.chart",
         "input": {"chart_type": "bar", "labels": ["A", "B"],
                   "values": [1, 2], "title": "A vs B"}, "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "have chart"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "plot this", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    # Untitled r1 never executed: titled r2 chart + final answer.
    assert [s.step_id for s in outcome.plan.steps] == ["r2", "r3"]
    assert outcome.result.step_results[-1].output == "have chart"


def test_react_exact_successful_repeat_is_idle() -> None:
    # Trace 07fb4f59 r2/r3: the identical Avg-Tokens chart executed twice.
    # An exact repeat of a success must idle, not re-execute.
    from app.orchestration.react import run_react

    chart = {"chart_type": "bar", "labels": ["A", "B"],
             "values": [46377, 21214], "title": "tokens"}
    provider = FakeLayeredProvider(queued=[
        {"thought": "plot tokens", "executor": "plot.chart",
         "input": dict(chart), "is_final": False},
        {"thought": "plot tokens again", "executor": "plot.chart",
         "input": dict(chart), "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "have chart"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "plot tokens as bar graph", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert [s.step_id for s in outcome.plan.steps] == ["r1", "r3"]
    assert outcome.result.step_results[-1].output == "have chart"


def test_react_replot_same_data_is_idle() -> None:
    # Trace 07fb4f59 r5/r6: [229, 135] re-plotted under different
    # labels/title renders the same bars. Same data (ignoring cosmetics)
    # must idle so the frontend never shows the same plot twice.
    from app.orchestration.react import _plot_data_key, run_react

    assert _plot_data_key(
        {"chart_type": "bar", "labels": ["A", "B"], "values": [229, 135],
         "title": "Number of Documents"}
    ) == _plot_data_key(
        {"chart_type": "bar", "labels": ["A (x)", "B (x)"], "values": [229, 135]}
    )
    assert _plot_data_key(
        {"chart_type": "bar", "labels": ["A", "B"], "values": [229, 135]}
    ) != _plot_data_key(
        {"chart_type": "bar", "labels": ["A", "B"], "values": [46377, 21214]}
    )

    provider = FakeLayeredProvider(queued=[
        {"thought": "plot docs", "executor": "plot.chart",
         "input": {"chart_type": "bar",
                   "labels": ["DocBench (documents)", "MMLongBench (documents)"],
                   "values": [229, 135], "title": "Number of Documents"},
         "is_final": False},
        {"thought": "plot docs again", "executor": "plot.chart",
          "input": {"chart_type": "bar", "labels": ["DocBench", "MMLongBench"],
                    "values": [229, 135], "title": "Doc counts (retitled)"},
          "is_final": False},
        {"thought": "done", "executor": "reasoning",
         "input": {}, "is_final": True, "answer": "have chart"},
    ])
    agents, tools = _react_orchestrator(provider)
    outcome = run_react(
        "plot docs as bar graph", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
    )
    assert [s.step_id for s in outcome.plan.steps] == ["r1", "r3"]


def test_react_synthesis_evidence_collapses_charts() -> None:
    # Trace 07fb4f59 r-final: raw SVG evidence invited an ASCII redraw.
    # Chart successes must collapse to a one-liner in synthesis evidence.
    from app.agents.base import StepStatus
    from app.orchestration.react import _synthesis_evidence_line
    from app.orchestration.results import StepResult

    chart = StepResult(
        step_id="r2", agent_id="plot.chart", status=StepStatus.SUCCESS,
        output="<svg xmlns='x'>...</svg>",
    )
    line = _synthesis_evidence_line(chart)
    assert "<svg" not in line and "Artifacts" in line
    text = StepResult(
        step_id="r1", agent_id="rag.query", status=StepStatus.SUCCESS,
        output="chunk text here",
    )
    assert "chunk text here" in _synthesis_evidence_line(text)


def test_react_retrieval_observation_not_truncated_to_1500() -> None:
    # Trace 7720c817: a whole-file return (~19k chars, 14 chunks) was sliced
    # to 1500 chars for the scratchpad, so the model saw the cover page plus
    # the start of the TOC and nothing else. The benchmark table sat ~12k
    # chars in, so the run concluded the document held no data. The evidence
    # must survive to the model, deep into the observation.
    from app.orchestration.react import (
        _RETRIEVAL_SCRATCHPAD_LIMIT,
        run_react,
    )

    class _BigRAG:
        def retrieve_context(
            self, notebook_id, query, top_k=4, file_id=None, file_name=None,
            mode="specific",
        ):
            # 14 chunks; the "benchmark table" lives deep in the dump.
            body = "\n\n".join(
                f"[{i} (r.pdf)] {'filler ' * 60}" for i in range(1, 14)
            ) + "\n\n[14 (r.pdf)] BENCHMARK TABLE: kNN 30s, LOF 40s"
            return {
                "query": query, "mode": mode,
                "results": [{
                    "content": body, "source": "r.pdf",
                    "section": "H1", "rerank_score": 0.9,
                }],
            }

    provider = FakeLayeredProvider(
        text="done",
        queued=[
            {"thought": "read it", "executor": "rag.query",
             "input": {"query": "benchmark comparison"}, "is_final": False},
            {"thought": "answer", "executor": "reasoning",
             "input": {}, "is_final": True, "answer": "react final"},
        ],
    )
    agents = get_default_agent_registry(provider)
    tools = get_default_tool_registry(_BigRAG(), provider)
    outcome = run_react(
        "plot the benchmark comparison", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
        notebook_context="1 file(s): r.pdf [ready] id=abc",
    )
    # The turn-2 prompt is the one that carries the observation.
    turn2 = next(
        m for m in provider.prompts
        if any("step r1 (rag.query)" in str(x.get("content", "")) for x in m)
    )
    seen = str(turn2[-1]["content"])
    assert _RETRIEVAL_SCRATCHPAD_LIMIT > 1500
    assert "BENCHMARK TABLE" in seen, (
        "evidence past the old 1500-char cut never reached the model"
    )
    # And the run still terminated on its own.
    assert outcome.result.step_results


def test_trim_scratchpad_drops_oldest_and_keeps_newest() -> None:
    from app.orchestration.react import _trim_scratchpad

    pad = ["a" * 30, "b" * 30, "c" * 30]
    kept = _trim_scratchpad(pad, 70)
    assert "".join(kept) == "b" * 30 + "c" * 30
    # Never empty: a single oversized entry still survives.
    assert _trim_scratchpad(["x" * 500], 10) == ["x" * 500]
    assert _trim_scratchpad([], 10) == []


def test_synthesis_evidence_line_keeps_full_retrieval() -> None:
    from app.agents.base import StepStatus
    from app.orchestration.react import _synthesis_evidence_line
    from app.orchestration.results import StepResult

    deep = "x" * 3000 + " BENCHMARK TABLE"
    line = _synthesis_evidence_line(
        StepResult(
            step_id="r1", agent_id="rag.query", status=StepStatus.SUCCESS,
            output=deep,
        )
    )
    assert "BENCHMARK TABLE" in line
    # Non-retrieval verdicts keep the short slice.
    short = _synthesis_evidence_line(
        StepResult(
            step_id="r5", agent_id="doc.convert", status=StepStatus.SUCCESS,
            output=deep,
        )
    )
    assert "BENCHMARK TABLE" not in short


def test_react_identical_retrieval_result_is_idle() -> None:
    # Trace 7720c817: five rag.query calls with five DIFFERENT query strings
    # all returned byte-identical chunks (the file only holds a cover page
    # and a TOC). `seen_actions` keys on input text so none tripped it, and
    # `failed_actions` only trips on failure — retrieval returning
    # useless-but-successful chunks counted as progress to both, so 5 of 6
    # iterations were burned on zero new evidence and the final synthesis
    # prompt carried the same chunk four times.
    from app.orchestration.react import run_react

    class _FixedRAG:
        """Same result regardless of query — the trace's exact shape."""

        calls = 0

        def retrieve_context(
            self, notebook_id, query, top_k=4, file_id=None, file_name=None,
            mode="specific",
        ):
            _FixedRAG.calls += 1
            return {
                "query": query,
                "mode": mode,
                "results": [
                    {
                        "content": "COVER PAGE ONLY. No benchmark table.",
                        "source": "r.pdf",
                        "section": "Cover",
                        "rerank_score": 0.5,
                    }
                ],
            }

    provider = FakeLayeredProvider(
        text="the document has no benchmark data",
        queued=[
            {"thought": "look", "executor": "rag.query",
             "input": {"query": "benchmark results"}, "is_final": False},
            {"thought": "look again", "executor": "rag.query",
             "input": {"query": "benchmark comparison metrics"}, "is_final": False},
            {"thought": "one more", "executor": "rag.query",
             "input": {"query": "accuracy precision recall"}, "is_final": False},
        ],
    )
    agents = get_default_agent_registry(provider)
    tools = get_default_tool_registry(_FixedRAG(), provider)
    outcome = run_react(
        "plot the benchmark comparison", provider, agents, tools,
        trace_id="t", notebook_id="nb-1",
        notebook_context="1 file(s): r.pdf [ready] id=abc",
    )
    # Only the first retrieval survives; the two repeats became idle turns and
    # the guard stopped the loop before a third wasted execution was recorded.
    rag_steps = [s for s in outcome.plan.steps if s.tool_id == "rag.query"]
    assert [s.step_id for s in rag_steps] == ["r1"]
    # And the repeated chunk is not pasted into the synthesis prompt.
    synth = outcome.plan.steps[-1]
    assert synth.step_id == "r-final"
    assert synth.input["message"].count("COVER PAGE ONLY") == 1
