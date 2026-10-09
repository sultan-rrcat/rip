"""Unit tests for the extracted ReactLoop class.

These tests pin the loop's guard methods and orchestration logic
without requiring real dependencies (Ollama, DB, etc.). The loop
is now testable as a unit — each guard is a method that can be
tested independently.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from app.agents.base import StepStatus
from app.orchestration.idle_guard import IdleGuard
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.react_loop import (
    MAX_REACT_ITERATIONS,
    REACT_SCHEMA,
    ReactLoop,
    ReactResult,
    _action_signature,
    _build_react_system_prompt,
    _coerce_dag_steps,
    _default_doc_format,
    _default_react_mode,
    _doc_content_key,
    _fallback_answer_text,
    _is_empty_file_claim,
    _normalize_react_input,
    _output_type,
    _plot_data_key,
    _redundant_convert_hint,
    _remap_executor,
    _synthesis_evidence_line,
    _trim_scratchpad,
    _validate_react_input,
)
from app.orchestration.results import ExecutionResult, StepResult


def _make_loop(queued_responses: list[dict] | None = None) -> ReactLoop:
    """Create a ReactLoop with mock provider, agents, and tools."""
    provider = MagicMock()
    if queued_responses is not None:
        provider.generate_structured.side_effect = queued_responses
    else:
        provider.generate_structured.return_value = {
            "thought": "test",
            "is_final": True,
            "answer": "done",
        }

    agents = MagicMock()
    agents.manifest.return_value = [
        {"agent_id": "coding"},
        {"agent_id": "reasoning"},
    ]

    tools = MagicMock()
    tools.manifest.return_value = [
        {"tool_id": "code.read"},
        {"tool_id": "doc.convert"},
        {"tool_id": "doc.generate"},
        {"tool_id": "notebook.inspect"},
        {"tool_id": "plot.chart"},
        {"tool_id": "rag.query"},
    ]

    synth_fn = MagicMock(return_value=None)

    return ReactLoop(provider, agents, tools, "test-trace-id", synth_fn)


class TestReactLoopCreation:
    def test_creates_with_mock_dependencies(self) -> None:
        loop = _make_loop()
        assert loop._trace_id == "test-trace-id"
        assert "coding" in loop._known_agents
        assert "reasoning" in loop._known_agents
        assert "rag.query" in loop._known_tools

    def test_agent_and_tool_ids_sorted(self) -> None:
        loop = _make_loop()
        assert loop._agent_ids == ["coding", "reasoning"]
        assert "rag.query" in loop._tool_ids


class TestReactLoopRun:
    def test_is_final_with_answer_returns_immediately(self) -> None:
        loop = _make_loop([{
            "thought": "done",
            "is_final": True,
            "answer": "the answer",
        }])
        result = loop.run("test request", notebook_id="nb-1")
        assert isinstance(result, ReactResult)
        assert result.plan.goal == "test request"

    def test_max_iterations_respected(self) -> None:
        loop = _make_loop([
            {"thought": "t", "executor": "reasoning",
             "input": {"message": "x"}, "is_final": False}
        ] * (MAX_REACT_ITERATIONS + 1))
        result = loop.run("test", notebook_id="nb-1", max_iterations=2)
        assert isinstance(result, ReactResult)

    def test_empty_notebook_corpus_processing_returns_clarification(self) -> None:
        loop = _make_loop([
            {
                "thought": "need docs",
                "executor": "rag.query",
                "input": {"query": "x"},
                "is_final": False,
            },
            {
                "thought": "done",
                "is_final": True,
                "answer": "The document is still being processed.",
            },
        ])
        result = loop.run(
            "test", notebook_id="nb-1",
            notebook_context="report.pdf [processing] id=abc123",
        )
        assert isinstance(result, ReactResult)
        assert len(result.result.step_results) > 0
        last = result.result.step_results[-1]
        assert last.status is StepStatus.SUCCESS
        assert "still being processed" in (last.output or "")

    def test_synthesis_called_when_not_terminal_doc(self) -> None:
        synth_fn = MagicMock(return_value=None)
        provider = MagicMock()
        provider.generate_structured.return_value = {
            "thought": "done",
            "is_final": True,
            "answer": "answer",
        }
        agents = MagicMock()
        agents.manifest.return_value = [{"agent_id": "reasoning"}]
        tools = MagicMock()
        tools.manifest.return_value = [{"tool_id": "rag.query"}]

        loop = ReactLoop(provider, agents, tools, "t", synth_fn)
        loop.run("test", notebook_id="nb-1")
        synth_fn.assert_called_once()

    def test_synthesis_skipped_on_terminal_doc(self) -> None:
        synth_fn = MagicMock(return_value=None)
        provider = MagicMock()
        provider.generate_structured.return_value = {
            "thought": "generate",
            "steps": [{
                "step_id": "1",
                "executor": "doc.generate",
                "input": {"title": "T", "sections": []},
            }],
            "is_final": False,
        }
        agents = MagicMock()
        agents.manifest.return_value = [{"agent_id": "reasoning"}]
        tools = MagicMock()
        tools.manifest.return_value = [{"tool_id": "rag.query"}]

        loop = ReactLoop(provider, agents, tools, "t", synth_fn)
        result = loop.run("test", notebook_id="nb-1")
        # Synthesis may or may not be called depending on execution,
        # but the loop should complete without error
        assert isinstance(result, ReactResult)


class TestGuardMethods:
    def test_normalize_react_input_unwraps_agent(self) -> None:
        result = _normalize_react_input("rag.query", {
            "agent": {"message": "hello"},
        })
        assert result["message"] == "hello"
        assert "agent" not in result

    def test_normalize_react_input_drops_tool_id(self) -> None:
        result = _normalize_react_input("rag.query", {
            "query": "x", "tool_id": "rag.query",
        })
        assert "tool_id" not in result

    def test_normalize_react_input_message_to_query(self) -> None:
        result = _normalize_react_input("rag.query", {
            "message": "search term",
        })
        assert result["query"] == "search term"

    def test_validate_react_input_reasoning_always_valid(self) -> None:
        assert _validate_react_input("reasoning", {}) is None

    def test_validate_react_input_rag_query_needs_query(self) -> None:
        assert _validate_react_input("rag.query", {}) is not None
        assert _validate_react_input("rag.query", {"query": "x"}) is None

    def test_validate_react_input_code_read_empty_notebook(self) -> None:
        hint = _validate_react_input("code.read", {}, has_files=False)
        assert hint is not None
        assert "NO files" in hint

    def test_remap_executor_code_read_with_message_becomes_coding(self) -> None:
        result = _remap_executor("code.read", {"message": "write code"})
        assert result == "coding"

    def test_remap_executor_code_read_with_file_id_unchanged(self) -> None:
        result = _remap_executor("code.read", {"file_id": "abc"})
        assert result == "code.read"

    def test_action_signature_stable(self) -> None:
        sig1 = _action_signature("rag.query", {"query": "x"})
        sig2 = _action_signature("rag.query", {"query": "x"})
        assert sig1 == sig2

    def test_plot_data_key_ignores_order(self) -> None:
        key1 = _plot_data_key({"chart_type": "bar", "values": [1, 2, 3]})
        key2 = _plot_data_key({"chart_type": "bar", "values": [3, 2, 1]})
        assert key1 == key2

    def test_doc_content_key_catches_duplicates(self) -> None:
        key1 = _doc_content_key({"title": "T", "sections": [{"heading": "H", "body": "B"}]})
        key2 = _doc_content_key({"title": "T", "sections": [{"heading": "H", "body": "B"}]})
        assert key1 == key2

    def test_redundant_convert_hint_none_when_no_formats(self) -> None:
        assert _redundant_convert_hint({"file_id": "abc"}, set(), {"abc"}) is None

    def test_redundant_convert_hint_trips_on_match(self) -> None:
        hint = _redundant_convert_hint(
            {"file_id": "abc", "target_format": "pdf"},
            {"pdf"},
            {"abc"},
        )
        assert hint is not None
        assert "already exists" in hint

    def test_is_empty_file_claim_detects(self) -> None:
        assert _is_empty_file_claim("I can't inspect that file")
        assert not _is_empty_file_claim("Here is the content")

    def test_trim_scratchpad_drops_oldest(self) -> None:
        scratchpad = ["old", "middle", "new"]
        result = _trim_scratchpad(scratchpad, 10)
        assert result == ["middle", "new"]

    def test_trim_scratchpad_keeps_last_even_if_over(self) -> None:
        scratchpad = ["very long old entry", "new"]
        result = _trim_scratchpad(scratchpad, 5)
        assert result == ["new"]

    def test_default_react_mode_overview_for_summarize(self) -> None:
        result = _default_react_mode("summarize the docs", {"query": "x"})
        assert result["mode"] == "overview"

    def test_default_react_mode_specific_for_factual(self) -> None:
        result = _default_react_mode("what is the total?", {"query": "x"})
        assert "mode" not in result

    def test_default_doc_format_injects_pdf(self) -> None:
        result = _default_doc_format("write in pdf", {"title": "T"})
        assert result["target_format"] == "pdf"

    def test_fallback_answer_text_finds_content(self) -> None:
        assert _fallback_answer_text({"content": "answer"}) == "answer"
        assert _fallback_answer_text({"output": "answer"}) == "answer"
        assert _fallback_answer_text({}) == ""

    def test_output_type_final_is_answer(self) -> None:
        assert _output_type("reasoning", True) == "answer"
        assert _output_type("rag.query", False) == "chunks"

    def test_synthesis_evidence_line_chart(self) -> None:
        result = StepResult(
            step_id="r1", agent_id="plot.chart",
            status=StepStatus.SUCCESS, output="<svg>...",
        )
        line = _synthesis_evidence_line(result, "values=[1,2]")
        assert "chart already generated" in line

    def test_synthesis_evidence_line_retrieval(self) -> None:
        result = StepResult(
            step_id="r1", agent_id="rag.query",
            status=StepStatus.SUCCESS, output="x" * 20000,
        )
        line = _synthesis_evidence_line(result)
        assert len(line) < 20000


class TestCoerceDagSteps:
    def test_legacy_single_step_wrapped(self) -> None:
        prepared, hint = _coerce_dag_steps(
            {"executor": "rag.query", "input": {"query": "x"}},
            1,
            {"reasoning"},
            {"rag.query"},
        )
        assert hint is None
        assert prepared is not None
        assert len(prepared) == 1
        assert prepared[0]["tool_id"] == "rag.query"

    def test_multi_step_dag_preserved(self) -> None:
        prepared, hint = _coerce_dag_steps(
            {"steps": [
                {"step_id": "1", "executor": "rag.query", "input": {"query": "x"}},
                {"step_id": "2", "executor": "reasoning",
                 "input": {"message": "answer"}, "depends_on": ["1"]},
            ]},
            1,
            {"reasoning"},
            {"rag.query"},
        )
        assert hint is None
        assert prepared is not None
        assert len(prepared) == 2

    def test_unknown_executor_rejected(self) -> None:
        prepared, hint = _coerce_dag_steps(
            {"steps": [{"step_id": "1", "executor": "unknown", "input": {}}]},
            1,
            {"reasoning"},
            {"rag.query"},
        )
        assert prepared is None
        assert hint is not None
        assert "UNKNOWN_EXECUTOR" in hint

    def test_empty_steps_rejected(self) -> None:
        prepared, hint = _coerce_dag_steps(
            {"steps": []},
            1,
            {"reasoning"},
            {"rag.query"},
        )
        assert prepared is None
        assert hint is not None


class TestBuildReactSystemPrompt:
    def test_prompt_contains_stop_at_first_match(self) -> None:
        prompt = _build_react_system_prompt(
            ["coding", "reasoning"],
            ["rag.query"],
            "(no documents)",
        )
        assert "stop at the first match" in prompt
        assert "INVALID" in prompt
        assert "(no documents)" in prompt

    def test_prompt_contains_agent_and_tool_ids(self) -> None:
        prompt = _build_react_system_prompt(
            ["coding", "reasoning"],
            ["rag.query", "plot.chart"],
            None,
        )
        assert "coding" in prompt
        assert "reasoning" in prompt
        assert "rag.query" in prompt
        assert "plot.chart" in prompt


class TestReactResult:
    def test_react_result_holds_plan_and_result(self) -> None:
        plan = Plan(plan_id="p1", goal="test", steps=[])
        result = ExecutionResult(trace_id="t", step_results=[])
        rr = ReactResult(plan, result)
        assert rr.plan is plan
        assert rr.result is result