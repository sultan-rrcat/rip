"""L3 ReAct — general fallback when no L2 deterministic builder applies.

Unlike upfront DAG planning, ReAct interleaves thought → action → observation:
each iteration proposes exactly ONE step, executes it immediately, and appends
the observation to the scratchpad. No placeholder wiring is ever emitted, so
the ecd93eb4 ungrounded-fan-in class cannot occur — inputs are inlined.

Bounded: max 6 iterations, cooperative cancel, per-step timeouts inherited
from run_plan_graph. Returns a (Plan, ExecutionResult) pair so the standard
deterministic Aggregator stays the single answer-assembly path.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from collections.abc import Callable

from app.agents.base import StepStatus
from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.observability.langfuse import (
    get_trace_context as _get_trace_context,
)
from app.observability.langfuse import (
    manual_span as _manual_span,
)
from app.observability.langfuse import (
    truncate as _truncate,
)
from app.orchestration.builders import _corpus_state
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.plan_graph import run_plan_graph
from app.orchestration.results import ExecutionResult, StepResult
from app.providers.base import ModelProvider
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.react")

MAX_REACT_ITERATIONS = 6

REACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "executor": {"type": "string"},
        "input": {"type": "object"},
        "is_final": {"type": "boolean"},
        "answer": {"type": "string"},
    },
    "required": ["thought", "executor", "is_final"],
}

_TOOL_OUTPUT_TYPES = {
    "rag.query": "chunks",
    "plot.chart": "chart",
    "doc.generate": "document",
    "doc.convert": "document",
    "image.generate": "document",
}

#: Correct-shape hints surfaced when the ReAct model emits a malformed
#: tool input (observed live trace c9e59039: {"agent": {"message": ...}}
#: for rag.query/code.sandbox, {"tool_id": ...} without target_format
#: for doc.convert — each burned a full iteration). Pre-flight validation
#: appends these to the scratchpad WITHOUT executing, so the 6-step
#: budget is preserved for real work.
_TOOL_INPUT_HINTS = {
    "rag.query": "rag.query needs {\"query\": \"...\"} flat "
    "(not {\"agent\": {...}}); add \"file_id\" to scope to one file",
    "code.sandbox": "code.sandbox needs {\"code\": \"...\"} flat "
    "(not {\"agent\": {...}})",
    "plot.chart": "plot.chart needs {\"chart_type\": \"bar|line\", "
    "\"labels\": [...], \"values\": [...] or \"series\": [{label, values}], "
    "plus a short \"title\" naming the metric and comparison "
    "(e.g. {\"title\": \"mAP@50-95: FASDD_CV vs AgniNetra\"}) "
    "with literal numbers from observations — never code.sandbox for charting",
    "doc.convert": "doc.convert needs {\"file_id\": \"...\", "
    "\"target_format\": \"md|docx|pdf\"}",
    "doc.generate": "doc.generate needs {\"title\": \"...\", "
    "\"sections\": [{\"heading\": ..., \"body\": ...}]}",
    "image.generate": "image.generate needs {\"message\": \"...\"}",
    "notebook.inspect": "notebook.inspect needs {} (notebook_id is injected)",
}


def _normalize_react_input(executor: str, action_input: dict) -> dict:
    """Unwrap common ReAct model slips into flat tool/agent inputs.

    - {"agent": {"message": ...}} → top-level "message" (observed live
      for rag.query AND code.sandbox in the same run).
    - stray {"tool_id": ...} inside input → dropped (executor already
      selects the tool; the key only confuses required-field checks).
    - rag.query message→query alias (mirrors RagQueryTool.execute);
      code.sandbox message→code alias (same recovery philosophy).
    Pure function — safe to unit test without Ollama/DB.
    """
    normalized = dict(action_input)
    nested = normalized.get("agent")
    if isinstance(nested, dict) and isinstance(nested.get("message"), str):
        if not str(normalized.get("message", "")).strip():
            normalized["message"] = nested["message"]
        normalized.pop("agent", None)
    normalized.pop("tool_id", None)
    if (
        executor == "rag.query"
        and not str(normalized.get("query", "")).strip()
        and str(normalized.get("message", "")).strip()
    ):
        normalized["query"] = str(normalized["message"]).strip()
    elif (
        executor == "code.sandbox"
        and not str(normalized.get("code", "")).strip()
        and str(normalized.get("message", "")).strip()
    ):
        normalized["code"] = str(normalized["message"]).strip()
    return normalized


def _validate_react_input(executor: str, action_input: dict) -> str | None:
    """Return None when valid, else a correct-shape hint string.

    Agents need input.message; tools need their flat schema fields.
    """
    if executor in ("reasoning", "coding", "vision"):
        return None  # agent message check lives at the call site
    if executor == "rag.query":
        if str(action_input.get("query", "")).strip():
            return None
        return _TOOL_INPUT_HINTS["rag.query"]
    if executor == "code.sandbox":
        if str(action_input.get("code", "")).strip():
            return None
        return _TOOL_INPUT_HINTS["code.sandbox"]
    if executor == "plot.chart":
        labels = action_input.get("labels")
        values = action_input.get("values")
        series = action_input.get("series")
        if not str(action_input.get("chart_type", "")).strip():
            return _TOOL_INPUT_HINTS["plot.chart"]
        if not isinstance(labels, list) or not labels:
            return _TOOL_INPUT_HINTS["plot.chart"]
        if "series_labels" in action_input:
            # Trace 07fb4f59 iter-4 shape: invented key alongside nested
            # values — the tool reads `series`, never `series_labels`.
            return (
                "plot.chart has no 'series_labels' field; for grouped "
                "comparisons pass 'series: [{label, values}]' with shared "
                "'labels', never nested 'values' arrays"
            )
        if series is not None:
            if values is not None:
                return (
                    "plot.chart takes either 'values' (single series) or "
                    "'series' (multi-series comparison), never both"
                )
            if not isinstance(series, list) or not series:
                return _TOOL_INPUT_HINTS["plot.chart"]
            for entry in series:
                if not isinstance(entry, dict) or not str(entry.get("label", "")).strip():
                    return (
                        "plot.chart 'series' entries must be "
                        "{label, values} objects with a non-empty label"
                    )
                entry_values = entry.get("values")
                if (
                    not isinstance(entry_values, list)
                    or not entry_values
                    or any(isinstance(v, (list, dict)) for v in entry_values)
                ):
                    return (
                        f"plot.chart series {entry.get('label')!r} 'values' "
                        "must be a flat array of numbers"
                    )
            if not str(action_input.get("title", "")).strip():
                return (
                    "plot.chart needs a short 'title' naming the metric "
                    "and comparison (e.g. 'mAP@50-95: FASDD_CV vs "
                    "AgniNetra'); retry with the same data plus a title"
                )
            return None
        # Single-series: values must be a flat array of numbers. Nested
        # arrays (trace 07fb4f59 iters 1+4) fail in the tool with
        # "'values' must all be numbers" — catch here as an idle turn so
        # the iteration budget is preserved for a corrected shape.
        if not isinstance(values, list) or not values:
            return _TOOL_INPUT_HINTS["plot.chart"]
        if any(isinstance(v, (list, dict)) for v in values):
            return (
                "plot.chart 'values' must be a flat array of numbers "
                "(one per label); for grouped comparisons use "
                "'series: [{label, values}]' with shared 'labels' instead "
                "of nesting arrays inside 'values'"
            )
        if not str(action_input.get("title", "")).strip():
            return (
                "plot.chart needs a short 'title' naming the metric "
                "and comparison (e.g. 'mAP@50-95: FASDD_CV vs "
                "AgniNetra'); retry with the same data plus a title"
            )
        return None
    if executor == "doc.convert":
        if str(action_input.get("file_id", "")).strip() and str(
            action_input.get("target_format", "")
        ).strip().lower() in ("md", "docx", "pdf"):
            return None
        return _TOOL_INPUT_HINTS["doc.convert"]
    if executor == "doc.generate":
        if str(action_input.get("title", "")).strip() and isinstance(
            action_input.get("sections"), list
        ):
            return None
        return _TOOL_INPUT_HINTS["doc.generate"]
    if executor == "image.generate":
        if str(action_input.get("message", "")).strip():
            return None
        return _TOOL_INPUT_HINTS["image.generate"]
    return None  # notebook.inspect + unknown tools: execution is the check


def _output_type(executor: str, is_final: bool) -> str:
    if is_final:
        return "answer"
    return _TOOL_OUTPUT_TYPES.get(executor, "text")


def _sandbox_available() -> bool:
    """Whether code.sandbox can execute on this host (docker CLI present).

    Split out for prompt advertisement + tests: on docker-less hosts the
    loop must never pick code.sandbox (trace 27dcf635 burned 2 of 6
    iterations on a deterministically-broken tool).
    """
    return shutil.which("docker") is not None


def _action_signature(executor: str, action_input: dict) -> str:
    """Stable id for a proposed action — repeats of a failed action loop out."""
    try:
        return executor + "\0" + json.dumps(action_input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return executor + "\0" + str(sorted(action_input))


def _plot_data_key(action_input: dict) -> tuple | None:
    """Normalized data identity for a plot.chart proposal.

    Same chart data under cosmetic tweaks (retitled, relabeled — trace
    07fb4f59 r5/r6 re-plotted [229, 135] with different labels) renders the
    same bars, so the frontend would show the same plot twice. The key
    covers chart_type + the numeric data only, ignoring title/labels/legend
    cosmetics. Returns None when the numbers cannot be read (validation
    owns that shape — this is a dedupe helper, not a validator).
    """
    try:
        chart_type = str(action_input.get("chart_type", "")).strip()
        series = action_input.get("series")
        if isinstance(series, list) and series:
            parts = []
            for entry in series:
                if not isinstance(entry, dict):
                    return None
                numbers = tuple(float(v) for v in (entry.get("values") or []))
                parts.append(numbers)
            return (chart_type, tuple(parts))
        values = action_input.get("values")
        if not isinstance(values, list):
            return None
        flat: list[float] = []
        for v in values:
            if isinstance(v, (list, dict)):
                return None
            flat.append(float(v))
        return (chart_type, tuple(flat))
    except (TypeError, ValueError):
        return None


def _chart_observation(action_input: dict) -> str:
    """Short scratchpad line for a successful chart — never raw SVG.

    Raw SVG observations (multi-KB) flood the scratchpad/synthesis context
    and teach the model nothing; the chart itself travels via the SSE
    artifacts event. Trace 07fb4f59's r-final redrew the charts as ASCII
    blocks because all it could see was SVG soup.
    """
    title = str(action_input.get("title", "") or "").strip()
    labels = action_input.get("labels")
    if title:
        return f"chart generated: {title}"
    if isinstance(labels, list) and labels:
        return f"chart generated for labels {labels}"
    return "chart generated"


def _synthesis_evidence_line(result) -> str:
    """One evidence line for the final-synthesis prompt.

    Chart successes collapse to a one-liner (the SVG bytes already travel
    via Artifacts; pasting them here only invites ASCII redraws like trace
    07fb4f59's r-final). Everything else keeps its truncated text.
    """
    if result.agent_id == "plot.chart" or (result.output or "").lstrip().startswith("<svg"):
        return f"[{result.step_id} (plot.chart)] chart already generated and shown in Artifacts"
    return f"[{result.step_id} ({result.agent_id})]\n{(result.output or '')[:1500]}"


#: Request keywords that need breadth (stratified sample), not topical rank.
#: The ReAct model defaults rag.query to mode=specific; without this hint a
#: "summarize the docs" loop retrieves REFERENCES sections repeatedly
#: (trace cfbaa9c3) instead of overview content.
_OVERVIEW_HINTS = (
    "summar", "overview", "compare", "contrast", "quiz",
    "overall", "main topics", "key points",
)


def _default_react_mode(request_text: str, action_input: dict) -> dict:
    """Default broad asks to overview retrieval (stratified one-per-H1).

    Pure helper: sets mode=overview only when the model left it unset and
    the request needs breadth. Single-fact QA keeps the specific default.
    """
    if str(action_input.get("mode", "")).strip():
        return action_input
    lowered = (request_text or "").lower()
    if any(h in lowered for h in _OVERVIEW_HINTS):
        action_input["mode"] = "overview"
    return action_input


class ReactResult:
    def __init__(self, plan: Plan, result: ExecutionResult):
        self.plan = plan
        self.result = result


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

    `parent_span_ctx` is the `react`-span context opened by the caller
    (orchestrator) — per-iteration spans parent explicitly under it so the
    trace reads `run → react → react:iter-N → step:rN` even across
    LangGraph pool-thread hops. None = parent to whatever is current
    (or no-op when tracing is off); existing callers are unaffected.
    """
    known_agents = {a["agent_id"] for a in agents.manifest()}
    known_tools = {t["tool_id"] for t in tools.manifest()}
    model = settings.ollama_default_model
    agent_ids = sorted(known_agents)
    tool_ids = sorted(known_tools)

    scratchpad: list[str] = []
    steps: list[PlanStep] = []
    step_results: list[StepResult] = []
    # Signatures of FAILED executions (trace 27dcf635 retried code.sandbox
    # twice with the same docker error and rag.query twice with the same
    # empty result). A proposed repeat becomes an idle turn — no execution,
    # budget preserved for a different executor or a final answer.
    failed_actions: dict[str, str] = {}
    # Signatures of SUCCESSFUL executions (trace 07fb4f59 plotted the
    # identical Avg-Tokens chart twice, r2 then r3 verbatim). An exact
    # repeat becomes an idle turn — the chart/answer already exists.
    seen_actions: set[str] = set()
    # Normalized chart data already plotted (chart_type + numbers, ignoring
    # title/labels cosmetics — trace 07fb4f59 r5/r6 re-plotted [229, 135]
    # under different labels). Re-plotting the same data renders the same
    # bars, so the frontend would show the same plot twice.
    plotted_data: set[tuple] = set()
    # rag.query is provably useless when the snapshot holds zero ready
    # files (same argument as the L2 empty-corpus short-circuit) — refuse
    # it once instead of burning iterations on "(no chunks retrieved)".
    corpus_empty = _corpus_state(notebook_context) in ("empty", "processing")
    sandbox_available = _sandbox_available()
    # Consecutive turns that produced no observation (provider errors,
    # unknown executors, empty answers). Caps garbage-loops against a
    # degraded model; any executed step or final answer resets it.
    idle_turns = 0

    # Explicit parent for `react:iter-N` spans: the `react`-span context
    # opened by the caller (orchestrator), or whatever is current when this
    # function is invoked directly (tests, ad-hoc). Plain strings — no
    # contextvars dependence across the thread hops below. No-op when
    # tracing is off.
    react_ctx = parent_span_ctx if parent_span_ctx is not None else _get_trace_context()
    final_answered = False
    for iteration in range(1, max_iterations + 1):
        with _manual_span(
            f"react:iter-{iteration}",
            as_type="span",
            input={
                "iteration": iteration,
                "request": _truncate(request_text, 500),
            },
            trace_context=react_ctx,
        ) as iter_obs:
            if cancel_event is not None and cancel_event.is_set():
                iter_obs.update(output={"status": "cancelled"})
                step_results.append(
                    StepResult(
                        step_id=f"r{iteration}", agent_id="react",
                        status=StepStatus.FAILURE, error="run cancelled",
                    )
                )
                break
            history = "\n".join(scratchpad) if scratchpad else "(no actions yet)"
            system_prompt = (
                "You are a ReAct agent. Answer the user request one step at a time.\n"
                f"Agents: {agent_ids}\nTools: {tool_ids}\n"
                "Each turn return thought (what you learned / what remains), "
                "executor (exactly one agent_id or tool_id for the NEXT single "
                "step), input, is_final (true only when answering now), and "
                "answer (the final answer when is_final).\n"
                "Input shape (FLAT object, never nested under 'agent'):\n"
                "- agent executor: {\"message\": \"...\"} with observations "
                "inlined verbatim — never reference steps by number.\n"
                "- tool executor: its FLAT schema fields, e.g. rag.query "
                "{\"query\": \"...\", \"file_id\": \"...\"}, plot.chart "
                "{\"chart_type\": \"bar\", \"labels\": [...], \"values\": [...], "
                "\"title\": \"<metric>: A vs B\"}, "
                "code.sandbox {\"code\": \"...\"}, doc.convert "
                "{\"file_id\": \"...\", \"target_format\": \"md|docx|pdf\"}.\n"
                "WRONG: {\"agent\": {\"message\": \"...\"}} for a tool — "
                "the tool reads top-level fields, so this fails with "
                "'query'/'code' required. RIGHT: {\"query\": \"...\"}.\n"
                "Prefer rag.query first when documents are available. "
                "For summarize/compare/quiz or 'overall content' asks use "
                "rag.query mode='overview' (stratified one-per-section "
                "sample); single-fact QA keeps the specific default. "
                "When the notebook has no documents and no observation "
                "holds numbers, recall approximate figures with a reasoning "
                "step first (state they are approximate), then plot.chart. "
                "Bar/line charts MUST use plot.chart with literal numbers "
                "from observations (or a prior reasoning step) — never "
                "code.sandbox for charting. Every plot.chart MUST include "
                "a short 'title' naming the metric and comparison "
                "(e.g. 'mAP@50-95: FASDD_CV vs AgniNetra'). "
                "Each plot.chart must cover a "
                "DIFFERENT metric — never re-plot numbers already charted; "
                "grouped comparisons use series:[{label, values}] with "
                "shared labels, never nested values arrays. "
                + (
                    ""
                    if sandbox_available
                    else "code.sandbox is UNAVAILABLE on this host (no "
                    "docker) — never pick it; use reasoning/plot.chart instead. "
                )
                + f"Notebook documents:\n{notebook_context or '(no documents)'}"
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Request: {request_text}\n\nScratchpad:\n{history}",
                },
            ]
            if context:
                messages.insert(
                    1, {"role": "system", "content": f"Conversation context:\n{context}"}
                )
            try:
                raw = provider.generate_structured(
                    model=model, messages=messages,
                    schema=REACT_SCHEMA, temperature=0,
                )
            except Exception as e:  # noqa: BLE001 - failed iteration is an observation
                idle_turns += 1
                if idle_turns >= 2:
                    # Provider itself is down — fail fast instead of burning the
                    # remaining iterations on identical errors.
                    iter_obs.update(output={
                        "status": "failed",
                        "error": _truncate(f"react planner unavailable: {e}", 500),
                    })
                    step_results.append(
                        StepResult(
                            step_id=f"r{iteration}", agent_id="react",
                            status=StepStatus.FAILURE,
                            error=f"react planner unavailable: {e}",
                        )
                    )
                    break
                scratchpad.append(f"planner error: {e}; propose a simpler next step.")
                iter_obs.update(output={
                    "status": "retry", "error": _truncate(str(e), 500),
                })
                continue
            executor = str(raw.get("executor", "")).strip()
            is_final = bool(raw.get("is_final", False))
            thought_in = _truncate(str(raw.get("thought", "")), 300)
            if is_final:
                answer = raw.get("answer") or ""
                if not str(answer).strip():
                    idle_turns += 1
                    if idle_turns >= 2:
                        iter_obs.update(output={
                            "status": "failed",
                            "error": "is_final with empty answer",
                        })
                        break
                    scratchpad.append("is_final was true but answer was empty; retry.")
                    iter_obs.update(output={"status": "retry", "is_final": True})
                    continue
                step_id = f"r{iteration}"
                steps.append(
                    PlanStep(
                        step_id=step_id, agent_id="reasoning",
                        input={"message": str(answer)},
                        expected_output_type="answer",
                    )
                )
                step_results.append(
                    StepResult(
                        step_id=step_id, agent_id="reasoning",
                        status=StepStatus.SUCCESS, output=str(answer),
                    )
                )
                final_answered = True
                iter_obs.update(output={
                    "status": "success",
                    "thought": thought_in,
                    "executor": "reasoning",
                    "is_final": True,
                    "answer": _truncate(str(answer), 2000),
                })
                break
            if executor not in known_agents and executor not in known_tools:
                idle_turns += 1
                if idle_turns >= 2:
                    iter_obs.update(output={
                        "status": "failed",
                        "error": _truncate(f"unknown executor {executor!r}", 500),
                    })
                    break
                scratchpad.append(
                    f"unknown executor {executor!r}; use one of {agent_ids + tool_ids}."
                )
                iter_obs.update(output={
                    "status": "retry",
                    "thought": thought_in,
                    "executor": executor,
                })
                continue
            raw_input = raw.get("input", {}) or {}
            action_input = _normalize_react_input(
                executor, dict(raw_input) if isinstance(raw_input, dict) else {}
            )
            if executor == "rag.query":
                action_input = _default_react_mode(request_text, action_input)
            if executor in known_tools:
                hint = _validate_react_input(executor, action_input)
                if hint is not None:
                    idle_turns += 1
                    if idle_turns >= 2:
                        iter_obs.update(output={
                            "status": "failed",
                            "error": _truncate(hint, 500),
                        })
                        break
                    scratchpad.append(f"{hint}; retry with corrected flat input.")
                    iter_obs.update(output={
                        "status": "retry",
                        "thought": thought_in,
                        "executor": executor,
                        "error": _truncate(hint, 500),
                    })
                    continue
            if executor in known_agents and not str(action_input.get("message", "")).strip():
                idle_turns += 1
                if idle_turns >= 2:
                    iter_obs.update(output={
                        "status": "failed",
                        "error": f"agent {executor} missing input.message",
                    })
                    break
                scratchpad.append(f"agent {executor} needs input.message; retry with it.")
                iter_obs.update(output={
                    "status": "retry",
                    "thought": thought_in,
                    "executor": executor,
                })
                continue
            if corpus_empty and executor == "rag.query":
                idle_turns += 1
                if idle_turns >= 2:
                    iter_obs.update(output={
                        "status": "failed",
                        "error": "rag.query on a notebook with no ready documents",
                    })
                    break
                scratchpad.append(
                    "notebook has no ready documents — rag.query cannot "
                    "return chunks; recall numbers with reasoning or answer "
                    "directly."
                )
                iter_obs.update(output={
                    "status": "retry",
                    "thought": thought_in,
                    "executor": executor,
                    "error": "rag.query refused: empty corpus",
                })
                continue
            sig = _action_signature(executor, action_input)
            if sig in failed_actions:
                idle_turns += 1
                if idle_turns >= 2:
                    iter_obs.update(output={
                        "status": "failed",
                        "error": _truncate(
                            f"{executor} already failed: {failed_actions[sig]}", 500
                        ),
                    })
                    break
                scratchpad.append(
                    f"{executor} already failed ({failed_actions[sig]}); pick "
                    "a different executor or answer from what you have."
                )
                iter_obs.update(output={
                    "status": "retry",
                    "thought": thought_in,
                    "executor": executor,
                    "error": _truncate(f"repeat of failed {executor}", 500),
                })
                continue
            if sig in seen_actions:
                idle_turns += 1
                if idle_turns >= 2:
                    iter_obs.update(output={
                        "status": "failed",
                        "error": _truncate(f"{executor} already did this exact step", 500),
                    })
                    break
                scratchpad.append(
                    f"{executor} with these exact inputs already succeeded; "
                    "do something different (a new metric, or answer from "
                    "what you have)."
                )
                iter_obs.update(output={
                    "status": "retry",
                    "thought": thought_in,
                    "executor": executor,
                    "error": _truncate(f"repeat of successful {executor}", 500),
                })
                continue
            if executor == "plot.chart":
                data_key = _plot_data_key(action_input)
                if data_key is not None and data_key in plotted_data:
                    idle_turns += 1
                    if idle_turns >= 2:
                        iter_obs.update(output={
                            "status": "failed",
                            "error": "chart data already plotted",
                        })
                        break
                    scratchpad.append(
                        "these numbers are already plotted in an earlier "
                        "chart; plot a DIFFERENT metric or answer from what "
                        "you have — never re-plot the same data."
                    )
                    iter_obs.update(output={
                        "status": "retry",
                        "thought": thought_in,
                        "executor": executor,
                        "error": "chart data already plotted",
                    })
                    continue
            idle_turns = 0
            step_id = f"r{iteration}"
            is_tool = executor in known_tools
            mini = Plan(
                plan_id=f"react-{iteration}",
                goal=request_text,
                steps=[
                    PlanStep(
                        step_id=step_id,
                        **({"tool_id": executor} if is_tool else {"agent_id": executor}),
                        input=action_input,
                        expected_output_type=_output_type(executor, False),
                    )
                ],
            )
            # Step spans parent explicitly under THIS iteration span so the
            # trace reads react → react:iter-N → step:rN even though the
            # inner plan graph schedules nodes on pool threads.
            step_parent = _get_trace_context()
            try:
                exec_result = run_plan_graph(
                    mini, agents, tool_registry=tools, trace_id=trace_id,
                    notebook_id=notebook_id, context=None,
                    fallback_message=request_text,
                    timeout_ms=timeout_ms or settings.default_timeout_ms,
                    on_event=on_event, cancel_event=cancel_event,
                    parent_span_ctx=step_parent,
                )
            except Exception as e:  # noqa: BLE001 - execution error is an observation
                scratchpad.append(f"step {step_id} ({executor}) raised {e}; try another.")
                iter_obs.update(output={
                    "status": "retry",
                    "thought": thought_in,
                    "executor": executor,
                    "error": _truncate(str(e), 500),
                })
                continue
            outcome = exec_result.step_results[0] if exec_result.step_results else None
            if outcome is None:
                scratchpad.append(f"step {step_id} produced nothing; try another.")
                iter_obs.update(output={
                    "status": "retry",
                    "thought": thought_in,
                    "executor": executor,
                })
                continue
            steps.append(mini.steps[0])
            step_results.append(outcome)
            idle_turns = 0  # an executed step is progress, even on tool failure
            seen_actions.add(sig)
            thought = str(raw.get("thought", ""))[:300]
            if outcome.status is StepStatus.SUCCESS:
                if is_tool and executor == "plot.chart":
                    data_key = _plot_data_key(action_input)
                    if data_key is not None:
                        plotted_data.add(data_key)
                    observation = _chart_observation(action_input)
                else:
                    observation = (outcome.output or "")[:1500]
                scratchpad.append(
                    f"step {step_id} ({executor}) thought: {thought} "
                    f"observation: {observation}"
                )
                iter_obs.update(output={
                    "status": "success",
                    "thought": thought_in,
                    "executor": executor,
                    "observation": _truncate(observation, 2000),
                })
            else:
                failed_actions[sig] = outcome.error or "unknown error"
                scratchpad.append(
                    f"step {step_id} ({executor}) failed: {outcome.error}; try another."
                )
                iter_obs.update(output={
                    "status": outcome.status.value,
                    "thought": thought_in,
                    "executor": executor,
                    "error": _truncate(outcome.error, 500),
                })
    if not final_answered:
        # Exhausted iterations without is_final (trace cfbaa9c3: six
        # rag.query observations, final summary was a truncated raw chunk
        # dump via the aggregator anti-blank fallback). Synthesize one
        # grounded answer from the successful observations so the user gets
        # prose covering every retrieved document instead of raw chunks.
        successes = [
            r for r in step_results
            if r.status is StepStatus.SUCCESS and (r.output or "").strip()
        ]
        if successes and not (cancel_event is not None and cancel_event.is_set()):
            synth_id = "reasoning" if "reasoning" in known_agents else (agent_ids[0] if agent_ids else "")
            if synth_id:
                evidence = "\n\n".join(
                    _synthesis_evidence_line(r) for r in successes[-4:]
                )
                synth_message = (
                    f"Synthesize the final answer to the request using ONLY "
                    f"these observations. Cover every document below; do not "
                    f"ask the user to upload or paste anything. Charts are "
                    f"already rendered in Artifacts — describe each chart's "
                    f"takeaway and give a summary table, but NEVER redraw "
                    f"charts as ASCII/text blocks. "
                    f"Request: {request_text}\n\nObservations:\n{evidence}"
                )
                try:
                    synth_plan = Plan(
                        plan_id=f"react-final-{uuid.uuid4().hex[:8]}",
                        goal=request_text,
                        steps=[
                            PlanStep(
                                step_id="r-final", agent_id=synth_id,
                                input={"message": synth_message},
                                expected_output_type="answer",
                            )
                        ],
                    )
                    synth_result = run_plan_graph(
                        synth_plan, agents, tool_registry=tools,
                        trace_id=trace_id, notebook_id=notebook_id,
                        context=context, fallback_message=request_text,
                        timeout_ms=timeout_ms or settings.default_timeout_ms,
                        on_event=on_event, cancel_event=cancel_event,
                        parent_span_ctx=_get_trace_context(),
                    )
                    synth_outcome = (
                        synth_result.step_results[0]
                        if synth_result.step_results else None
                    )
                    if synth_outcome is not None and synth_outcome.status is StepStatus.SUCCESS:
                        steps.append(synth_plan.steps[0])
                        step_results.append(synth_outcome)
                        final_answered = True
                except Exception as e:  # noqa: BLE001 - synthesis miss keeps raw steps
                    logger.warning("react final synthesis failed: %s", e)
    plan = Plan(
        plan_id=str(uuid.uuid4()), goal=request_text,
        steps=steps or [
            PlanStep(
                step_id="r0", agent_id="reasoning",
                input={"message": request_text}, expected_output_type="text",
            )
        ],
    )
    if not step_results:
        step_results = [
            StepResult(
                step_id="r0", agent_id="react",
                status=StepStatus.FAILURE,
                error="react loop produced no steps",
            )
        ]
    return ReactResult(plan, ExecutionResult(trace_id=trace_id, step_results=step_results))
