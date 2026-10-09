"""L3 ReAct engine — general fallback when no L2 deterministic builder applies.

Thin wrapper around `ReactLoop` (extracted for testability). The loop
lives on `ReactLoop`; this module keeps the pure helpers, the
`ReActEngine` façade, and the `run_react` entry point.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

from app.agents.base import StepStatus
from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.observability.langfuse import get_trace_context as _get_trace_context
from app.observability.langfuse import truncate as _truncate
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.plan_graph import run_plan_graph
from app.orchestration.react_loop import (
    MAX_DAG_STEPS_PER_ITERATION,
    MAX_PLOT_CHARTS_PER_RUN,
    MAX_REACT_ITERATIONS,
    REACT_SCHEMA,
    ReactLoop,
    ReactResult,
    _ANSWER_FALLBACK_FIELDS,
    _CODE_READ_SCRATCHPAD_LIMIT,
    _EMPTY_FILE_CLAIM_HINTS,
    _OVERVIEW_HINTS,
    _RETRIEVAL_SCRATCHPAD_LIMIT,
    _SCRATCHPAD_TOTAL_LIMIT,
    _TOOL_INPUT_HINTS,
    _TOOL_OUTPUT_TYPES,
    _action_signature,
    _chart_observation,
    _code_read_hint,
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
from app.providers.base import ModelProvider
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.react")


class ReActEngine:
    """Thought → action → observation loop with a single `run()` interface.

    The loop itself lives on `ReactLoop`; this class binds
    composition-time dependencies and delegates.
    """

    def __init__(
        self,
        provider: ModelProvider,
        agents: AgentRegistry,
        tools: ToolRegistry,
        trace_id: str,
    ):
        self._provider = provider
        self._agents = agents
        self._tools = tools
        self._trace_id = trace_id
        self._known_agents = {a["agent_id"] for a in agents.manifest()}
        self._known_tools = {t["tool_id"] for t in tools.manifest()}
        self._agent_ids = sorted(self._known_agents)
        self._tool_ids = sorted(self._known_tools)
        self._model = settings.ollama_default_model

    def run(
        self,
        request_text: str,
        *,
        notebook_id: str | None,
        context: str | None = None,
        notebook_context: str | None = None,
        max_iterations: int = MAX_REACT_ITERATIONS,
        timeout_ms: int | None = None,
        cancel_event: threading.Event | None = None,
        on_event: Callable[[dict], None] | None = None,
        parent_span_ctx: dict[str, str] | None = None,
    ) -> ReactResult:
        loop = ReactLoop(
            self._provider,
            self._agents,
            self._tools,
            self._trace_id,
            synth_fn=self._synthesize_final_answer,
        )
        return loop.run(
            request_text,
            notebook_id=notebook_id,
            context=context,
            notebook_context=notebook_context,
            max_iterations=max_iterations,
            timeout_ms=timeout_ms,
            cancel_event=cancel_event,
            on_event=on_event,
            parent_span_ctx=parent_span_ctx,
        )

    def _synthesize_final_answer(
        self,
        request_text: str,
        step_results: list[StepResult],
        *,
        context: str | None,
        notebook_id: str | None,
        timeout_ms: int | None,
        cancel_event: threading.Event | None,
        on_event: Callable[[dict], None] | None,
        chart_fingerprints: dict[str, str] | None = None,
        hint_answer: str | None = None,
    ) -> tuple[PlanStep, StepResult] | None:
        """Grounded final answer, synthesized OUTSIDE the ReACT loop.

        The loop's is_final `answer` field is unreliable as the deliverable
        (it is framed amid internal thought/executor reasoning, so it tends
        to hallucinate structure and leak steps); it arrives here only as a
        `hint_answer` seed. One fresh call phrases the user-facing answer
        from the loop's successful observations (or, when no tools ran —
        e.g. greetings — polishes the planner's draft). Returns None only
        when there is nothing at all to work from (no observations AND no
        hint), or the call fails.
        """
        successes = [
            r
            for r in step_results
            if r.status is StepStatus.SUCCESS and (r.output or "").strip()
        ]
        cancelled = cancel_event is not None and cancel_event.is_set()
        if not cancelled and (successes or (hint_answer or "").strip()):
            synth_id = (
                "reasoning"
                if "reasoning" in self._known_agents
                else (self._agent_ids[0] if self._agent_ids else "")
            )
            if synth_id:
                if successes:
                    fingerprints = chart_fingerprints or {}
                    evidence_lines: list[str] = []
                    seen_evidence: set[str] = set()
                    for r in successes[-4:]:
                        line = _synthesis_evidence_line(r, fingerprints.get(r.step_id, ""))
                        if line not in seen_evidence:
                            seen_evidence.add(line)
                            evidence_lines.append(line)
                    evidence = "\n\n".join(evidence_lines)
                else:
                    evidence = f"(no tool observations were collected)\n\nPlanner draft:\n{hint_answer}"
                has_code_evidence = any(
                    (r.agent_id or "").lower() in ("code.read", "coding")
                    for r in successes
                )
                lowered_request = (request_text or "").lower()
                wants_code = has_code_evidence or any(
                    k in lowered_request
                    for k in ("test script", "test file", "unit test",
                              "write code", "code", "script", ".py")
                )
                if successes and wants_code:
                    synth_message = (
                        f"Synthesize the final answer to the request using ONLY "
                        f"these observations. When file content is present "
                        f"below, write the requested code/test script FROM that "
                        f"content (complete and runnable, fenced code blocks "
                        f"with the right language tag, then a brief usage "
                        f"note). Cover every file below. If no file content "
                        f"is present, say honestly which file could not be "
                        f"read and name it so the user can retry with the "
                        f"file name — never invent file content. Write like "
                        f"a world-class assistant in clear Markdown; never "
                        f"expose step ids or internal machinery. "
                        f"Request: {request_text}\n\nObservations:\n{evidence}"
                    )
                elif not successes:
                    synth_message = (
                        f"Write the final reply to the request. No tools were "
                        f"needed. Use the planner's draft below as the "
                        f"starting point: keep what answers the request, fix "
                        f"the framing, and remove any trace of internal "
                        f"reasoning, step ids, or tool machinery. Write like a "
                        f"world-class assistant: direct answer first, then "
                        f"supporting detail in clear Markdown. Never invent "
                        f"content beyond the draft.\n\n"
                        f"Request: {request_text}\n\nDraft:\n{hint_answer}"
                    )
                elif any(
                    fmt in lowered_request for fmt in ("pdf", "docx", "md")
                ):
                    has_doc = any(
                        (r.agent_id or "").lower() == "doc.generate"
                        and r.status is StepStatus.SUCCESS
                        for r in successes
                    )
                    has_chart = any(
                        (r.agent_id or "").lower() == "plot.chart"
                        for r in successes
                    )
                    if not has_doc and not has_chart:
                        synth_message = (
                            f"Synthesize the final answer to the request using ONLY "
                            f"these observations. No document/chart artifact was "
                            f"produced in this run, so do NOT claim one exists and "
                            f"do NOT claim inability to produce one — the tool "
                            f"exists but was not called. Present the drafted content "
                            f"below verbatim in clear Markdown (direct answer first, "
                            f"then detail); never expose step ids or internal "
                            f"machinery. "
                            f"Request: {request_text}\n\nObservations:\n{evidence}"
                        )
                    else:
                        synth_message = (
                            f"Synthesize the final answer to the request using ONLY "
                            f"these observations. Cover every document below. "
                            f"Charts are already rendered in Artifacts — describe "
                            f"each chart's takeaway and give a summary table, but "
                            f"NEVER redraw charts as ASCII/text blocks. The "
                            f"document/chart is already produced; just describe "
                            f"it and summarize, do not rebuild it. If the "
                            f"observations show no usable content, say honestly "
                            f"what was tried and which file is needed — never "
                            f"invent content. Write like a world-class assistant: "
                            f"lead with the direct answer, then supporting detail in "
                            f"clear Markdown (short headings, bullets, numbered "
                            f"steps, or a table when it helps); never expose step "
                            f"ids or internal machinery. "
                            f"Request: {request_text}\n\nObservations:\n{evidence}"
                        )
                else:
                    synth_message = (
                        f"Synthesize the final answer to the request using ONLY "
                        f"these observations. Cover every document below. "
                        f"Charts are already rendered in Artifacts — describe "
                        f"each chart's takeaway and give a summary table, but "
                        f"NEVER redraw charts as ASCII/text blocks. The "
                        f"document/chart is already produced; just describe "
                        f"it and summarize, do not rebuild it. If the "
                        f"observations show no usable content, say honestly "
                        f"what was tried and which file is needed — never "
                        f"invent content. Write like a world-class assistant: "
                        f"lead with the direct answer, then supporting detail in "
                        f"clear Markdown (short headings, bullets, numbered "
                        f"steps, or a table when it helps); never expose step "
                        f"ids or internal machinery. "
                        f"Request: {request_text}\n\nObservations:\n{evidence}"
                    )
                try:
                    synth_plan = Plan(
                        plan_id=f"react-final-{uuid.uuid4().hex[:8]}",
                        goal=request_text,
                        steps=[
                            PlanStep(
                                step_id="r-final",
                                agent_id=synth_id,
                                input={"message": synth_message},
                                expected_output_type="answer",
                            )
                        ],
                    )
                    synth_result = run_plan_graph(
                        synth_plan,
                        self._agents,
                        tool_registry=self._tools,
                        trace_id=self._trace_id,
                        notebook_id=notebook_id,
                        context=context,
                        fallback_message=request_text,
                        timeout_ms=timeout_ms or settings.default_timeout_ms,
                        on_event=on_event,
                        cancel_event=cancel_event,
                        parent_span_ctx=_get_trace_context(),
                    )
                    synth_outcome = (
                        synth_result.step_results[0]
                        if synth_result.step_results
                        else None
                    )
                    if (
                        synth_outcome is not None
                        and synth_outcome.status is StepStatus.SUCCESS
                    ):
                        return synth_plan.steps[0], synth_outcome
                except Exception as e:  # noqa: BLE001 - synthesis miss keeps raw steps
                    logger.warning("react final synthesis failed: %s", e)
        return None


def run_react(
    request_text: str,
    provider: ModelProvider,
    agents: AgentRegistry,
    tools: ToolRegistry,
    *,
    trace_id: str,
    notebook_id: str | None,
    context: str | None = None,
    notebook_context: str | None = None,
    max_iterations: int = MAX_REACT_ITERATIONS,
    timeout_ms: int | None = None,
    cancel_event: threading.Event | None = None,
    on_event: Callable[[dict], None] | None = None,
    parent_span_ctx: dict[str, str] | None = None,
) -> ReactResult:
    """Run the thought → action → observation loop to answer request_text.

    Thin wrapper over `ReActEngine` — preserved so existing callers
    (orchestrator, tests) are unaffected. `parent_span_ctx` is the
    `react`-span context opened by the caller (orchestrator).
    """
    engine = ReActEngine(provider, agents, tools, trace_id)
    return engine.run(
        request_text,
        notebook_id=notebook_id,
        context=context,
        notebook_context=notebook_context,
        max_iterations=max_iterations,
        timeout_ms=timeout_ms,
        cancel_event=cancel_event,
        on_event=on_event,
        parent_span_ctx=parent_span_ctx,
    )