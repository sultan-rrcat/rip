"""L3 ReAct loop — the thought → action → observation cycle.

Extracted from `react_engine.ReActEngine.run()` so the loop is testable
as a unit. All guard state lives on the class; each guard check is a
method that returns a hint string when it trips (or None when the
action is valid). The `run()` method is a thin orchestrator that calls
these methods.

The synthesis callback (`synth_fn`) is injected so the loop stays
independent of the answer-phrasing strategy.
"""

from __future__ import annotations

import json
import logging
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
from app.orchestration.corpus import _snapshot_files, get_corpus_state
from app.orchestration.idle_guard import IdleGuard
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.plan_graph import run_plan_graph
from app.orchestration.results import ExecutionResult, StepResult
from app.orchestration.validator import PlanValidator
from app.providers.base import ModelProvider
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.react")

MAX_REACT_ITERATIONS = 6
MAX_PLOT_CHARTS_PER_RUN = 3

REACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "executor": {"type": "string"},
        "input": {"type": "object"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string"},
                    "executor": {"type": "string"},
                    "input": {"type": "object"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "expected_output_type": {"type": "string"},
                },
                "required": ["executor", "input"],
            },
        },
        "is_final": {"type": "boolean"},
        "answer": {"type": "string"},
    },
    "required": ["thought", "is_final"],
}

MAX_DAG_STEPS_PER_ITERATION = 5

_TOOL_OUTPUT_TYPES = {
    "rag.query": "chunks",
    "plot.chart": "chart",
    "doc.generate": "document",
    "doc.convert": "document",
    "code.read": "text",
    "notebook.inspect": "text",
}

_TOOL_INPUT_HINTS = {
    "rag.query": 'rag.query needs {"query": "..."} flat '
    '(not {"agent": {...}}); add "file_id" to scope to one file',
    "plot.chart": 'plot.chart needs {"chart_type": "bar|line", '
    '"labels": [...], "values": [...] or "series": [{label, values}], '
    'plus a short "title" naming the metric and comparison '
    '(e.g. {"title": "mAP@50-95: FASDD_CV vs AgniNetra"}) '
    "with literal numbers from observations",
    "doc.convert": 'doc.convert needs {"file_id": "...", '
    '"target_format": "md|docx|pdf"}',
    "doc.generate": 'doc.generate needs {"title": "...", '
    '"sections": [{"heading": ..., "body": ...}]}',
    "notebook.inspect": "notebook.inspect needs {} (notebook_id is injected)",
}

_RETRIEVAL_EXECUTORS = frozenset({"rag.query", "code.read"})

_CODE_READ_SCRATCHPAD_LIMIT = 8000
_RETRIEVAL_SCRATCHPAD_LIMIT = 12000
_SCRATCHPAD_TOTAL_LIMIT = 40000

_OVERVIEW_HINTS = (
    "summar",
    "overview",
    "compare",
    "contrast",
    "quiz",
    "overall",
    "main topics",
    "key points",
)

_ANSWER_FALLBACK_FIELDS = ("answer", "content", "message", "text", "body", "output")

_EMPTY_FILE_CLAIM_HINTS = (
    "can't inspect",
    "cannot inspect",
    "no content was attached",
    "no file content available",
    "haven't actually attached",
    "havenot actually attached",
    "won't guess at what's inside",
    "not have access to files referenced by id",
)


def _code_read_hint(has_files: bool) -> str:
    if has_files:
        return (
            'code.read needs {"file_id": "..."} (literal id from '
            "notebook.inspect, never a placeholder) or {\"file_name\": "
            '"..."}; call it FIRST for code tasks, then pass its content into '
            "coding"
        )
    return (
        "this notebook has NO files, so code.read cannot work — do not call "
        "it. Write the code directly with the coding agent: executor "
        '"coding" with {"message": "<the full task, data included>"}. For a '
        "chart from numbers in the message, use plot.chart instead."
    )


def _normalize_react_input(executor: str, action_input: dict) -> dict:
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
    return normalized


def _validate_react_input(
    executor: str, action_input: dict, *, has_files: bool = True
) -> str | None:
    if executor == "reasoning":
        return None
    if executor == "rag.query":
        if str(action_input.get("query", "")).strip():
            return None
        return _TOOL_INPUT_HINTS["rag.query"]
    if executor == "plot.chart":
        labels = action_input.get("labels")
        values = action_input.get("values")
        series = action_input.get("series")
        if not str(action_input.get("chart_type", "")).strip():
            return _TOOL_INPUT_HINTS["plot.chart"]
        if not isinstance(labels, list) or not labels:
            return _TOOL_INPUT_HINTS["plot.chart"]
        if "series_labels" in action_input:
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
                if (
                    not isinstance(entry, dict)
                    or not str(entry.get("label", "")).strip()
                ):
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
        if not has_files:
            return (
                "this notebook has NO files, so doc.convert cannot work "
                "— do not call it. Answer directly with is_final=true "
                "(e.g. greetings/small-talk) or ask what to produce."
            )
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
    if executor == "code.read":
        if not has_files:
            return _code_read_hint(False)
        fid = str(action_input.get("file_id", "") or "").strip()
        fname = str(action_input.get("file_name", "") or "").strip()
        if fid or fname:
            if fid and ("{{" in fid or "}}" in fid):
                return (
                    "'file_id' must be a literal snapshot id from "
                    "notebook.inspect, never a {{id}} placeholder"
                )
            return None
        return _code_read_hint(has_files)
    return None


def _output_type(executor: str, is_final: bool) -> str:
    if is_final:
        return "answer"
    return _TOOL_OUTPUT_TYPES.get(executor, "text")


def _infer_generic_executor(action_input: dict) -> str | None:
    keys = set(action_input or {})
    if "sections" in keys and "title" in keys:
        return "doc.generate"
    if "chart_type" in keys:
        return "plot.chart"
    if "query" in keys:
        return "rag.query"
    if "target_format" in keys:
        return "doc.convert"
    if "file_id" in keys or "file_name" in keys:
        return "code.read"
    if "message" in keys:
        return "reasoning"
    if not keys:
        return "notebook.inspect"
    return None


def _coerce_dag_steps(
    raw: dict,
    iteration: int,
    known_agents: set[str],
    known_tools: set[str],
) -> tuple[list[dict] | None, str | None]:
    raw_steps = raw.get("steps")
    if raw_steps is None:
        executor = str(raw.get("executor", "") or "").strip()
        if not executor:
            return None, "turn proposed no steps and no executor"
        raw_input = raw.get("input", {}) or {}
        if not isinstance(raw_input, dict):
            return None, "step input must be an object"
        raw_steps = [
            {
                "step_id": f"r{iteration}",
                "executor": executor,
                "input": raw_input,
            }
        ]
    if not isinstance(raw_steps, list) or not raw_steps:
        return None, "turn proposed an empty steps array"
    if len(raw_steps) > MAX_DAG_STEPS_PER_ITERATION:
        return (
            None,
            (
                f"turn proposed {len(raw_steps)} steps "
                f"(max {MAX_DAG_STEPS_PER_ITERATION}); split across iterations"
            ),
        )
    id_map: dict[str, str] = {}
    for idx, rs in enumerate(raw_steps, start=1):
        if not isinstance(rs, dict):
            return None, f"step {idx} is not an object"
        raw_id = str(rs.get("step_id") or str(idx)).strip() or str(idx)
        assigned = f"r{iteration}_{idx}" if len(raw_steps) > 1 else f"r{iteration}"
        id_map[raw_id] = assigned
        id_map[str(idx)] = assigned
    prepared: list[dict] = []
    for idx, rs in enumerate(raw_steps, start=1):
        if not isinstance(rs, dict):
            return None, f"step {idx} is not an object"
        executor = str(rs.get("executor", "") or "").strip()
        if not executor:
            return None, f"step {idx} is missing executor"
        if executor.lower() in ("tool", "agent", "function"):
            inferred = _infer_generic_executor(rs.get("input", {}) or {})
            if inferred is not None:
                executor = inferred
        if executor not in known_agents and executor not in known_tools:
            return None, f"UNKNOWN_EXECUTOR:{executor}"
        action_input = rs.get("input", {}) or {}
        if not isinstance(action_input, dict):
            return None, f"step {idx} input must be an object"
        action_input = _normalize_react_input(executor, dict(action_input))
        executor = _remap_executor(executor, action_input)
        if executor not in known_agents and executor not in known_tools:
            return None, f"UNKNOWN_EXECUTOR:{executor}"
        raw_id = str(rs.get("step_id") or str(idx)).strip() or str(idx)
        step_id = id_map.get(raw_id, f"r{iteration}_{idx}")
        depends_on: list[str] = []
        raw_deps = rs.get("depends_on", []) or []
        if not isinstance(raw_deps, list):
            return None, f"step {idx} depends_on must be an array"
        for dep in raw_deps:
            depends_on.append(id_map.get(str(dep), str(dep)))
        eot = rs.get("expected_output_type") or _output_type(executor, False)
        is_tool = executor in known_tools
        step_dict: dict = {
            "step_id": step_id,
            ("tool_id" if is_tool else "agent_id"): executor,
            "input": action_input,
            "depends_on": depends_on,
            "expected_output_type": str(eot),
        }
        prepared.append(step_dict)
    return prepared, None


def _fallback_answer_text(action_input: dict) -> str:
    for field in _ANSWER_FALLBACK_FIELDS:
        value = action_input.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_empty_file_claim(output: str | None) -> bool:
    lowered = (output or "").lower()
    return any(h in lowered for h in _EMPTY_FILE_CLAIM_HINTS)


def _trim_scratchpad(scratchpad: list[str], limit: int) -> list[str]:
    if not scratchpad:
        return scratchpad
    kept = list(scratchpad)
    total = sum(len(line) for line in kept)
    while len(kept) > 1 and total > limit:
        total -= len(kept.pop(0))
    return kept


def _remap_executor(executor: str, action_input: dict) -> str:
    if executor != "code.read":
        return executor
    has_file_ref = any(
        str(action_input.get(k, "") or "").strip() for k in ("file_id", "file_name")
    )
    has_message = bool(str(action_input.get("message", "") or "").strip())
    if has_file_ref or not has_message:
        return executor
    logger.info(
        "react: remapping misnamed code.read (no file id, message present) -> coding"
    )
    return "coding"


def _action_signature(executor: str, action_input: dict) -> str:
    try:
        return executor + "\0" + json.dumps(action_input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return executor + "\0" + str(sorted(action_input))


def _plot_data_key(action_input: dict) -> tuple | None:
    try:
        chart_type = str(action_input.get("chart_type", "")).strip()
        series = action_input.get("series")
        if isinstance(series, list) and series:
            all_values = []
            for entry in series:
                if not isinstance(entry, dict):
                    return None
                vals = entry.get("values")
                if not isinstance(vals, list):
                    return None
                for v in vals:
                    if isinstance(v, (list, dict)):
                        return None
                    all_values.append(round(float(v), 2))
            return (chart_type, tuple(sorted(all_values)))
        values = action_input.get("values")
        if not isinstance(values, list):
            return None
        flat = []
        for v in values:
            if isinstance(v, (list, dict)):
                return None
            flat.append(round(float(v), 2))
        return (chart_type, tuple(sorted(flat)))
    except (TypeError, ValueError):
        return None


def _doc_content_key(action_input: dict) -> tuple | None:
    try:
        title = str(action_input.get("title", "")).strip()
        sections = action_input.get("sections")
        if not isinstance(sections, list) or not sections:
            return None
        parts = [title]
        for s in sections:
            if not isinstance(s, dict):
                return None
            parts.append(str(s.get("heading", "")))
            parts.append(str(s.get("body", "")))
        return tuple(parts)
    except (TypeError, ValueError):
        return None


def _chart_observation(action_input: dict) -> str:
    title = str(action_input.get("title", "") or "").strip()
    series = action_input.get("series")
    values = action_input.get("values")
    data_bits = []
    if isinstance(series, list) and series:
        for entry in series:
            if isinstance(entry, dict):
                name = str(entry.get("label", "")).strip()
                vals = entry.get("values")
                if isinstance(vals, list):
                    data_bits.append(f"{name}={vals}")
    elif isinstance(values, list):
        data_bits.append(f"values={values}")
    result = "chart generated"
    if title:
        result += f": {title}"
    if data_bits:
        result += " [" + ", ".join(data_bits) + "]"
    return result


def _synthesis_evidence_line(result, fingerprint: str = "") -> str:
    if result.agent_id == "plot.chart" or (result.output or "").lstrip().startswith(
        "<svg"
    ):
        if fingerprint:
            return f"[{result.step_id} (plot.chart)] chart already generated: {fingerprint}"
        return f"[{result.step_id} (plot.chart)] chart already generated and shown in Artifacts"
    limit = (
        _RETRIEVAL_SCRATCHPAD_LIMIT
        if result.agent_id in _RETRIEVAL_EXECUTORS
        else 1500
    )
    return f"[{result.step_id} ({result.agent_id})]\n{(result.output or '')[:limit]}"


def _default_doc_format(request_text: str, action_input: dict) -> dict:
    if str(action_input.get("target_format", "")).strip():
        return action_input
    lowered = (request_text or "").lower()
    for fmt in ("pdf", "docx", "md"):
        if fmt in lowered:
            updated = dict(action_input)
            updated["target_format"] = fmt
            return updated
    return action_input


def _default_react_mode(request_text: str, action_input: dict) -> dict:
    if str(action_input.get("mode", "")).strip():
        return action_input
    lowered = (request_text or "").lower()
    if any(h in lowered for h in _OVERVIEW_HINTS):
        updated = dict(action_input)
        updated["mode"] = "overview"
        return updated
    return action_input


def _redundant_convert_hint(
    action_input: dict,
    generated_formats: set[str],
    source_ids: set[str],
) -> str | None:
    if not generated_formats:
        return None
    target = str(action_input.get("target_format", "")).strip().lower()
    if target not in generated_formats:
        return None
    fid = action_input.get("file_id")
    if not (isinstance(fid, str) and fid.strip() in source_ids):
        return None
    return (
        f"a doc.generate report in {target} format already exists in this "
        f"run — converting source file {fid.strip()!r} to {target} only "
        f"re-renders the ORIGINAL upload, never the report just built; "
        f"answer from what you have (is_final=true), do not run doc.convert."
    )


#: Admin-editable ReAct prompt template (see app/core/promptstore.py).
#: Placeholders (required — PUT refuses bodies missing them):
#:   {agent_ids}         — sorted agent ids for this run
#:   {tool_ids}          — sorted ENABLED tool ids for this run
#:   {notebook_context}  — snapshot text or "(no documents)"
#: Rendered with plain .replace (never .format): the prose carries literal
#: JSON braces and {{id}} placeholders that .format would interpolate.
REACT_PROMPT_TEMPLATE = (
    "You are a ReAct agent. Output EXACTLY one JSON object per turn: "
    '{"thought","steps","is_final","answer"}. '
    "One COMPLETE DAG per turn (1-5 steps); if it fails you get the next iteration to repair it.\n"
    "Agents: {agent_ids}\nTools: {tool_ids}\n"
    "Shape (mandatory, never violated):\n"
    '- each steps[] element is {"step_id": "1", "executor": "<one of coding, reasoning, code.read, code.sandbox, doc.convert, doc.generate, notebook.inspect, plot.chart, rag.query>", "input": {...}, '
    '"depends_on": ["1"], "expected_output_type": "answer|text|chunks|numbers|chart|document"}. '
    'Legacy single-step {"executor","input"} is still accepted as a 1-step DAG.\n'
    '- input is a FLAT object, never nested under \'agent\'. WRONG: {"agent": {"message": "..."}}. RIGHT: {"query": "..."}.\n'
    '- agent step REQUIRES {"message": "<full task text>"} with observations inlined verbatim — never reference steps by number. A turn with an agent step but no input.message is INVALID.\n'
    "- tool step REQUIRES its flat fields: "
    'rag.query {"query": "...", "file_id": "..."}, '
    'plot.chart {"chart_type": "bar", "labels": [...], "values": [...], "title": "<metric>: A vs B"}, '
    'doc.convert {"file_id": "...", "target_format": "md|docx|pdf"}, '
    'doc.generate {"title": "...", "sections": [{"heading": ..., "body": ...}], "target_format": "md|docx|pdf"}, '
    "notebook.inspect {}, "
    'code.read {"file_id": "..."} or {"file_name": "..."}, '
    'code.sandbox {"notebook_id": "...", "task": "<full ask>", "file_names": ["..."]} '
    "(executes code in an isolated container and returns stdout plus changed files).\n"
    '- wire dataflow with depends_on + {{id}} placeholders: downstream input must contain {{1}} for depends_on ["1"]. '
    "A step with depends_on but no placeholder, or a placeholder with no edge, is INVALID.\n"
    "Decide in order, stop at the first match:\n"
    "1. Greeting/small talk with no task: is_final=true, answer directly, no steps.\n"
    "2. Empty notebook (snapshot shows no documents/files): NEVER rag.query/code.read/doc.convert — they return nothing. "
    "For write/create/generate/draft asks (email/letter/report in pdf/docx/md): "
    'emit ONE DAG with 2 steps: step 1 reasoning {"message": "Draft <deliverable> for: <request>. Use [brackets] for unknown details (name/date/recipient)."}, '
    'step 2 doc.generate {"title", "sections": [{"heading","body": full draft verbatim}], "target_format": pdf/docx/md from the request} '
    'with depends_on ["1"] — do NOT emit the draft alone and wait; the DAG must contain both steps. '
    "Draft with placeholders FIRST — never ask clarifying questions INSTEAD of drafting; put follow-ups in the final answer.\n"
    "3. CODE tasks (write/test/explain/review/debug code): notebook.inspect once, then code.read each needed file, "
    "then ONE coding step with file content inlined verbatim in {\"message\": \"...\"}. "
    "For run/test/execute asks (run the code, run tests, fix failures by running): "
    "use code.sandbox with the full ask and file scope instead — report only what the box returned, never invent execution results. "
    "Never coding without file content in an observation (empty notebook with no files: write directly with coding, no code.read).\n"
    "4. Retrieval when documents exist: rag.query first "
    "(overview for summarize/compare/quiz/overall-content, specific default otherwise). "
    "doc.generate creates a NEW report file from answer text (its pdf/docx output IS the deliverable); "
    "doc.convert only re-renders an ORIGINAL upload named in the snapshot — never convert a source file to satisfy a report ask.\n"
    "5. Charts: bar/line MUST use plot.chart with literal numbers from observations (or a prior reasoning step) plus a short title naming metric and comparison. "
    "When the notebook has no documents and no observation holds numbers, recall approximate figures with a reasoning step first (state they are approximate), then plot.chart. "
    "Each plot.chart covers a DIFFERENT metric; grouped comparisons use series:[{label, values}] with shared labels, never nested values arrays.\n"
    "6. Final: is_final=true carries the polished user-facing Markdown answer (direct answer first, then detail; never expose thought/executor/step numbers). Empty answer is invalid.\n"
    "doc.generate sections must carry the COMPLETE user-visible answer (full text verbatim, never a stub) — the file renders ONLY sections.\n"
    "Notebook documents:\n{notebook_context}"
)


def _build_react_system_prompt(
    agent_ids: list[str], tool_ids: list[str], notebook_context: str | None
) -> str:
    from app.core.promptstore import get_prompt

    template = get_prompt("react.system_prompt")
    return (
        template.replace("{agent_ids}", str(agent_ids))
        .replace("{tool_ids}", str(tool_ids))
        .replace("{notebook_context}", notebook_context or "(no documents)")
    )


class ReactResult:
    def __init__(self, plan: Plan, result: ExecutionResult):
        self.plan = plan
        self.result = result


class ReactLoop:
    """The ReAct thought → action → observation loop.

    Holds all guard state as instance variables. Each guard check is a
    method that returns a hint string when it trips (or None when the
    action is valid). The `run()` method is a thin orchestrator that
    calls these methods.

    The synthesis callback (`synth_fn`) is injected so the loop stays
    independent of the answer-phrasing strategy.
    """

    def __init__(
        self,
        provider: ModelProvider,
        agents: AgentRegistry,
        tools: ToolRegistry,
        trace_id: str,
        synth_fn: Callable[..., tuple[PlanStep, StepResult] | None],
    ):
        self._provider = provider
        self._agents = agents
        self._tools = tools
        self._trace_id = trace_id
        self._synth_fn = synth_fn
        self._known_agents = {a["agent_id"] for a in agents.manifest()}
        # Enabled-only: a disabled tool reads as an unknown executor, so the
        # loop repairs instead of executing it (executor also guards races).
        # Derived from manifest entries (missing flag = enabled) so doubles
        # that stub manifest() keep working.
        self._known_tools = {
            t["tool_id"] for t in tools.manifest() if t.get("enabled", True)
        }
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
        known_agents = self._known_agents
        known_tools = self._known_tools
        agent_ids = self._agent_ids
        tool_ids = self._tool_ids

        scratchpad: list[str] = []
        steps: list[PlanStep] = []
        step_results: list[StepResult] = []
        failed_actions: dict[str, str] = {}
        seen_actions: set[str] = set()
        plotted_data: set[tuple] = set()
        chart_fingerprints: dict[str, str] = {}
        seen_doc_content: set[tuple] = set()
        corpus_state = get_corpus_state(notebook_context)
        corpus_empty = corpus_state in ("empty", "processing")
        corpus_processing = corpus_state == "processing"
        has_files = bool(_snapshot_files(notebook_context))
        source_ids = {
            fid for _, _, fid in _snapshot_files(notebook_context) if fid
        } | {"*"}
        generated_formats: set[str] = set()
        lowered_req = (request_text or "").lower()
        doc_wanted = any(fmt in lowered_req for fmt in ("pdf", "docx", "md"))
        consecutive_reasoning = 0
        seen_observations: dict[str, str] = {}
        guard = IdleGuard()

        react_ctx = (
            parent_span_ctx if parent_span_ctx is not None else _get_trace_context()
        )
        final_hint_answer = ""
        no_step_reason = ""

        def _loop_on_event(event: dict) -> None:
            if on_event is not None and event.get("type") != "delta":
                on_event(event)

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
                            step_id=f"r{iteration}",
                            agent_id="react",
                            status=StepStatus.FAILURE,
                            error="run cancelled",
                        )
                    )
                    break
                history = (
                    "\n".join(_trim_scratchpad(scratchpad, _SCRATCHPAD_TOTAL_LIMIT))
                    if scratchpad
                    else "(no actions yet)"
                )
                system_prompt = _build_react_system_prompt(
                    agent_ids, tool_ids, notebook_context
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
                        1,
                        {
                            "role": "system",
                            "content": f"Conversation context:\n{context}",
                        },
                    )
                try:
                    with _manual_span(
                        "react:planner",
                        as_type="span",
                        input={
                            "iteration": iteration,
                            "scratchpad": _truncate(history, 1000),
                        },
                    ) as planner_obs:
                        raw = self._provider.generate_structured(
                            model=self._model,
                            messages=messages,
                            schema=REACT_SCHEMA,
                            temperature=0,
                            timeout_ms=settings.planner_timeout_ms,
                            cancel_event=cancel_event,
                        )
                        planner_obs.update(output={
                            "thought": _truncate(str(raw.get("thought", "")), 300),
                            "executor": raw.get("executor"),
                            "is_final": bool(raw.get("is_final", False)),
                        })
                except Exception as e:
                    if guard.record_idle():
                        iter_obs.update(
                            output={
                                "status": "failed",
                                "idle_guard": True,
                                "idle_reason": "planner unavailable",
                                "error": _truncate(
                                    f"react planner unavailable: {e}", 500
                                ),
                            }
                        )
                        step_results.append(
                            StepResult(
                                step_id=f"r{iteration}",
                                agent_id="react",
                                status=StepStatus.FAILURE,
                                error=f"react planner unavailable: {e}",
                            )
                        )
                        break
                    scratchpad.append(
                        f"planner error: {e}; propose a simpler next step."
                    )
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "rejection_reason": "planner error",
                            "error": _truncate(str(e), 500),
                        }
                    )
                    continue
                executor = str(raw.get("executor", "")).strip()
                is_final = bool(raw.get("is_final", False))
                thought_in = _truncate(str(raw.get("thought", "")), 300)
                if is_final:
                    if isinstance(raw.get("steps"), list) and raw.get("steps"):
                        if not str(raw.get("answer") or "").strip():
                            is_final = False
                        else:
                            if guard.record_idle():
                                iter_obs.update(
                                    output={
                                        "status": "failed",
                                        "idle_guard": True,
                                        "idle_reason": "is_final with steps",
                                        "error": "is_final with steps",
                                    }
                                )
                                no_step_reason = (
                                    "the assistant marked itself finished while also "
                                    "proposing steps to execute"
                                )
                                break
                            scratchpad.append(
                                "is_final=true must not carry steps[]; set is_final=false "
                                "to execute the DAG, or is_final=true with answer only "
                                "and no steps."
                            )
                            iter_obs.update(output={
                                "status": "retry",
                                "rejection_reason": "is_final with steps",
                                "is_final": True,
                            })
                            continue
                    if is_final:
                        answer = raw.get("answer") or ""
                        if not str(answer).strip():
                            raw_input = raw.get("input", {}) or {}
                            answer = _fallback_answer_text(
                                dict(raw_input) if isinstance(raw_input, dict) else {}
                            )
                        if not str(answer).strip():
                            if guard.record_idle():
                                iter_obs.update(
                                    output={
                                        "status": "failed",
                                        "idle_guard": True,
                                        "idle_reason": "is_final with empty answer",
                                        "error": "is_final with empty answer",
                                    }
                                )
                                no_step_reason = (
                                    "the assistant kept marking itself finished "
                                    "without producing an answer"
                                )
                                break
                            scratchpad.append(
                                "is_final was true but answer was empty. Either put the "
                                "final answer text in the 'answer' field with "
                                "is_final=true, or set is_final=false and execute a "
                                "tool step (e.g. doc.generate with title+sections)."
                            )
                            iter_obs.update(output={
                                "status": "retry",
                                "rejection_reason": "is_final with empty answer",
                                "is_final": True,
                            })
                            continue
                        final_hint_answer = str(answer)
                        guard.record_progress()
                        iter_obs.update(
                            output={
                                "status": "success",
                                "thought": thought_in,
                                "executor": "reasoning",
                                "is_final": True,
                                "answer": _truncate(str(answer), 2000),
                                "final_via": "synthesis",
                            }
                        )
                        break
                raw_dag = raw.get("steps")
                if isinstance(raw_dag, list) and raw_dag:
                    dag_error: str | None = None
                    prepared, coerce_hint = _coerce_dag_steps(
                        raw, iteration, known_agents, known_tools
                    )
                    if prepared is None:
                        dag_error = coerce_hint or "malformed steps array"
                        if dag_error.startswith("UNKNOWN_EXECUTOR:"):
                            bad = dag_error.split(":", 1)[1]
                            if guard.record_idle():
                                iter_obs.update(
                                    output={
                                        "status": "failed",
                                        "idle_guard": True,
                                        "idle_reason": "unknown executor",
                                        "error": _truncate(f"unknown executor {bad!r}", 500),
                                    }
                                )
                                break
                            scratchpad.append(
                                f"unknown executor {bad!r}; use one of {agent_ids + tool_ids}."
                            )
                            iter_obs.update(
                                output={
                                    "status": "retry",
                                    "rejection_reason": "unknown executor",
                                    "thought": thought_in,
                                    "executor": bad,
                                }
                            )
                            continue
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "malformed DAG",
                                    "error": _truncate(dag_error, 500),
                                }
                            )
                            break
                        scratchpad.append(f"{dag_error}; retry with a corrected DAG.")
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "malformed DAG",
                                "thought": thought_in,
                                "error": _truncate(dag_error, 500),
                            }
                        )
                        continue
                    for ps in prepared:
                        ex = ps.get("tool_id") or ps.get("agent_id") or ""
                        if ex == "rag.query":
                            ps["input"] = _default_react_mode(request_text, ps["input"])
                        if ex == "doc.generate":
                            ps["input"] = _default_doc_format(request_text, ps["input"])
                    preflight_hint: str | None = None
                    preflight_exec = ""
                    for ps in prepared:
                        ex = ps.get("tool_id") or ps.get("agent_id") or ""
                        inp = ps.get("input", {}) or {}
                        if ex in known_agents and not str(inp.get("message", "")).strip():
                            preflight_hint = (
                                f"agent {ex} needs input.message; retry with it"
                            )
                            preflight_exec = ex
                            break
                        if ex in known_tools:
                            hint = _validate_react_input(ex, inp, has_files=has_files)
                            if hint is not None:
                                preflight_hint = f"{hint}; retry with corrected flat input"
                                preflight_exec = ex
                                break
                        if corpus_empty and ex == "rag.query":
                            preflight_hint = "rag.query refused: empty corpus"
                            preflight_exec = ex
                            break
                        if ex == "doc.convert":
                            dup = _redundant_convert_hint(inp, generated_formats, source_ids)
                            if dup is not None:
                                preflight_hint = dup
                                preflight_exec = ex
                                break
                        sig = _action_signature(ex, inp)
                        if sig in failed_actions:
                            preflight_hint = (
                                f"{ex} already failed ({failed_actions[sig]}); "
                                "propose a different DAG"
                            )
                            preflight_exec = ex
                            break
                        if sig in seen_actions:
                            preflight_hint = (
                                f"{ex} with these exact inputs already succeeded; "
                                "propose a different DAG"
                            )
                            preflight_exec = ex
                            break
                    if preflight_hint is not None:
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "DAG pre-flight failed",
                                    "error": _truncate(preflight_hint, 500),
                                }
                            )
                            break
                        scratchpad.append(preflight_hint)
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "DAG pre-flight failed",
                                "thought": thought_in,
                                "executor": preflight_exec,
                                "error": _truncate(preflight_hint, 500),
                            }
                        )
                        continue
                    try:
                        dag_plan = Plan.from_model(
                            plan_id=f"react-{iteration}",
                            goal=request_text,
                            raw_steps=prepared,
                        )
                        PlanValidator(self._agents, self._tools).validate(dag_plan)
                    except Exception as e:
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "DAG validation failed",
                                    "error": _truncate(str(e), 500),
                                }
                            )
                            break
                        scratchpad.append(
                            f"DAG validation failed: {e}; retry with depends_on + "
                            "{{id}} placeholders fixed."
                        )
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "DAG validation failed",
                                "thought": thought_in,
                                "error": _truncate(str(e), 500),
                            }
                        )
                        continue
                    guard.record_progress()
                    step_parent = _get_trace_context()
                    try:
                        exec_result = run_plan_graph(
                            dag_plan,
                            self._agents,
                            tool_registry=self._tools,
                            trace_id=self._trace_id,
                            notebook_id=notebook_id,
                            context=None,
                            fallback_message=request_text,
                            timeout_ms=timeout_ms or settings.default_timeout_ms,
                            on_event=_loop_on_event if on_event is not None else None,
                            cancel_event=cancel_event,
                            parent_span_ctx=step_parent,
                        )
                    except Exception as e:
                        scratchpad.append(
                            f"iteration {iteration} DAG raised {e}; repair it next iteration."
                        )
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "thought": thought_in,
                                "executors": [s.executor_id for s in dag_plan.steps],
                                "error": _truncate(str(e), 500),
                            }
                        )
                        continue
                    outcomes = list(exec_result.step_results or [])
                    if not outcomes:
                        scratchpad.append(
                            f"iteration {iteration} DAG produced nothing; try another DAG."
                        )
                        iter_obs.update(
                            output={"status": "retry", "thought": thought_in}
                        )
                        continue
                    thought = str(raw.get("thought", ""))[:300]
                    for outcome in outcomes:
                        ex = outcome.agent_id
                        sid = outcome.step_id
                        steps.append(
                            next(s for s in dag_plan.steps if s.step_id == sid)
                        )
                        step_results.append(outcome)
                        seen_actions.add(
                            _action_signature(
                                ex,
                                next(
                                    s.input
                                    for s in dag_plan.steps
                                    if s.step_id == sid
                                ),
                            )
                        )
                        if outcome.status is StepStatus.SUCCESS:
                            if ex in _RETRIEVAL_EXECUTORS and outcome.output:
                                if outcome.output in seen_observations:
                                    continue
                                seen_observations[outcome.output] = sid
                            if ex == "plot.chart":
                                for s in dag_plan.steps:
                                    if s.step_id == sid:
                                        dk = _plot_data_key(s.input)
                                        if dk is not None:
                                            plotted_data.add(dk)
                                        chart_fingerprints[sid] = _chart_observation(s.input)
                                        break
                            if ex == "doc.generate":
                                for s in dag_plan.steps:
                                    if s.step_id == sid:
                                        ck = _doc_content_key(s.input)
                                        if ck is not None:
                                            seen_doc_content.add(ck)
                                        gt = str(s.input.get("target_format") or "md").strip().lower()
                                        if gt in ("md", "docx", "pdf"):
                                            generated_formats.add(gt)
                                        break
                            if ex == "rag.query" or ex == "code.read":
                                lim = (
                                    _RETRIEVAL_SCRATCHPAD_LIMIT
                                    if ex in _RETRIEVAL_EXECUTORS
                                    else _CODE_READ_SCRATCHPAD_LIMIT
                                )
                                obs = (outcome.output or "")[:lim]
                            elif ex == "plot.chart":
                                obs = chart_fingerprints.get(sid, "chart generated")
                            else:
                                obs = (outcome.output or "")[:1500]
                            scratchpad.append(
                                f"step {sid} ({ex}) thought: {thought} observation: {obs}"
                            )
                        else:
                            failed_actions[
                                _action_signature(
                                    ex,
                                    next(
                                        s.input
                                        for s in dag_plan.steps
                                        if s.step_id == sid
                                    ),
                                )
                            ] = outcome.error or "unknown error"
                            scratchpad.append(
                                f"step {sid} ({ex}) failed: {outcome.error}; "
                                f"repair the DAG next iteration."
                            )
                    guard.record_progress()
                    consecutive_reasoning = 0
                    failed_in_dag = [o for o in outcomes if o.status is not StepStatus.SUCCESS]
                    iter_obs.update(
                        output={
                            "status": "success" if not failed_in_dag else "partial",
                            "thought": thought_in,
                            "executors": [o.agent_id for o in outcomes],
                            "observation": _truncate(
                                "; ".join(
                                    f"{o.step_id}:{o.status.value}" for o in outcomes
                                ),
                                500,
                            ),
                        }
                    )
                    if not failed_in_dag and any(
                        o.agent_id == "doc.generate" for o in outcomes
                    ):
                        break
                    continue
                executor = str(raw.get("executor", "") or "").strip()
                if not executor and raw.get("steps") is not None:
                    if guard.record_idle():
                        iter_obs.update(
                            output={
                                "status": "failed",
                                "idle_guard": True,
                                "idle_reason": "empty DAG",
                                "error": "turn proposed an empty steps array",
                            }
                        )
                        break
                    scratchpad.append("turn proposed an empty steps array; propose a DAG.")
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "rejection_reason": "empty DAG",
                            "thought": thought_in,
                        }
                    )
                    continue
                if executor not in known_agents and executor not in known_tools:
                    if guard.record_idle():
                        iter_obs.update(
                            output={
                                "status": "failed",
                                "idle_guard": True,
                                "idle_reason": "unknown executor",
                                "error": _truncate(
                                    f"unknown executor {executor!r}", 500
                                ),
                            }
                        )
                        break
                    scratchpad.append(
                        f"unknown executor {executor!r}; use one of {agent_ids + tool_ids}."
                    )
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "rejection_reason": "unknown executor",
                            "thought": thought_in,
                            "executor": executor,
                        }
                    )
                    continue
                raw_input = raw.get("input", {}) or {}
                action_input = _normalize_react_input(
                    executor, dict(raw_input) if isinstance(raw_input, dict) else {}
                )
                executor = _remap_executor(executor, action_input)
                if executor == "rag.query":
                    action_input = _default_react_mode(request_text, action_input)
                if executor == "doc.generate":
                    action_input = _default_doc_format(request_text, action_input)
                if executor in known_tools:
                    hint = _validate_react_input(
                        executor, action_input, has_files=has_files
                    )
                    if hint is not None:
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "validation failed",
                                    "error": _truncate(hint, 500),
                                }
                            )
                            break
                        scratchpad.append(f"{hint}; retry with corrected flat input.")
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "validation failed",
                                "thought": thought_in,
                                "executor": executor,
                                "error": _truncate(hint, 500),
                            }
                        )
                        continue
                if (
                    executor in known_agents
                    and not str(action_input.get("message", "")).strip()
                ):
                    if guard.record_idle():
                        iter_obs.update(
                            output={
                                "status": "failed",
                                "idle_guard": True,
                                "idle_reason": "agent missing input.message",
                                "error": f"agent {executor} missing input.message",
                            }
                        )
                        break
                    scratchpad.append(
                        f"agent {executor} needs input.message; retry with it."
                    )
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "rejection_reason": "agent missing input.message",
                            "thought": thought_in,
                            "executor": executor,
                        }
                    )
                    continue
                if corpus_empty and executor == "rag.query":
                    if guard.record_idle():
                        no_step_reason = (
                            "the document is still being processed, so there "
                            "are no chunks to read yet"
                            if corpus_processing
                            else "this notebook has no ready documents"
                        )
                        iter_obs.update(
                            output={
                                "status": "failed",
                                "idle_guard": True,
                                "idle_reason": (
                                    "rag.query while processing"
                                    if corpus_processing
                                    else "rag.query on empty corpus"
                                ),
                                "error": (
                                    "rag.query on a document that is still "
                                    "being processed"
                                    if corpus_processing
                                    else "rag.query on a notebook with no "
                                    "ready documents"
                                ),
                            }
                        )
                        break
                    scratchpad.append(
                        
                            "the notebook's document is STILL PROCESSING — it "
                            "has no chunks to read yet, so rag.query cannot "
                            "return anything. Do NOT retry it. Set "
                            "is_final=true and tell the user to wait until "
                            "processing finishes, then retry the request."
                            if corpus_processing
                            else "notebook has no ready documents — rag.query "
                            "cannot return chunks; answer directly with "
                            "is_final=true (e.g. greetings/small-talk) or "
                            "recall numbers with reasoning."
                        
                    )
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "rejection_reason": (
                                "rag.query while processing"
                                if corpus_processing
                                else "rag.query on empty corpus"
                            ),
                            "thought": thought_in,
                            "executor": executor,
                            "error": (
                                "rag.query refused: document still processing"
                                if corpus_processing
                                else "rag.query refused: empty corpus"
                            ),
                        }
                    )
                    continue
                if executor == "doc.convert":
                    dup_hint = _redundant_convert_hint(
                        action_input, generated_formats, source_ids
                    )
                    if dup_hint is not None:
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "redundant doc.convert",
                                    "error": "doc.convert refused: report already generated",
                                }
                            )
                            break
                        scratchpad.append(dup_hint)
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "redundant doc.convert",
                                "thought": thought_in,
                                "executor": executor,
                                "error": "doc.convert refused: redundant convert",
                            }
                        )
                        continue
                sig = _action_signature(executor, action_input)
                if sig in failed_actions:
                    if guard.record_idle():
                        iter_obs.update(
                            output={
                                "status": "failed",
                                "idle_guard": True,
                                "idle_reason": "repeat of failed action",
                                "error": _truncate(
                                    f"{executor} already failed: {failed_actions[sig]}",
                                    500,
                                ),
                            }
                        )
                        break
                    scratchpad.append(
                        f"{executor} already failed ({failed_actions[sig]}); pick "
                        "a different executor or answer from what you have."
                    )
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "rejection_reason": "repeat of failed action",
                            "thought": thought_in,
                            "executor": executor,
                            "error": _truncate(f"repeat of failed {executor}", 500),
                        }
                    )
                    continue
                if sig in seen_actions:
                    if guard.record_idle():
                        iter_obs.update(
                            output={
                                "status": "failed",
                                "idle_guard": True,
                                "idle_reason": "repeat of successful action",
                                "error": _truncate(
                                    f"{executor} already did this exact step", 500
                                ),
                            }
                        )
                        break
                    scratchpad.append(
                        f"{executor} with these exact inputs already succeeded; "
                        "do something different (a new metric, or answer from "
                        "what you have)."
                    )
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "rejection_reason": "repeat of successful action",
                            "thought": thought_in,
                            "executor": executor,
                            "error": _truncate(f"repeat of successful {executor}", 500),
                        }
                    )
                    continue
                if executor == "plot.chart":
                    data_key = _plot_data_key(action_input)
                    if data_key is not None and data_key in plotted_data:
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "chart data already plotted",
                                    "error": "chart data already plotted",
                                }
                            )
                            break
                        scratchpad.append(
                            "these numbers are already plotted in an earlier "
                            "chart; plot a DIFFERENT metric or answer from what "
                            "you have — never re-plot the same data."
                        )
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "chart data already plotted",
                                "thought": thought_in,
                                "executor": executor,
                                "error": "chart data already plotted",
                            }
                        )
                        continue
                if executor == "doc.generate":
                    content_key = _doc_content_key(action_input)
                    if content_key is not None and content_key in seen_doc_content:
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "doc.generate content already succeeded",
                                    "error": "doc.generate with this content already succeeded",
                                }
                            )
                            break
                        scratchpad.append(
                            "doc.generate with this exact content already succeeded; "
                            "use the existing document as the final answer or set "
                            "is_final=true with a summary of it."
                        )
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "doc.generate content already succeeded",
                                "thought": thought_in,
                                "executor": executor,
                                "error": "repeat of successful doc.generate (content match)",
                            }
                        )
                        continue
                guard.record_progress()
                step_id = f"r{iteration}"
                is_tool = executor in known_tools
                mini = Plan(
                    plan_id=f"react-{iteration}",
                    goal=request_text,
                    steps=[
                        PlanStep(
                            step_id=step_id,
                            **(
                                {"tool_id": executor}
                                if is_tool
                                else {"agent_id": executor}
                            ),
                            input=action_input,
                            expected_output_type=_output_type(executor, False),
                        )
                    ],
                )
                step_parent = _get_trace_context()
                try:
                    exec_result = run_plan_graph(
                        mini,
                        self._agents,
                        tool_registry=self._tools,
                        trace_id=self._trace_id,
                        notebook_id=notebook_id,
                        context=None,
                        fallback_message=request_text,
                        timeout_ms=timeout_ms or settings.default_timeout_ms,
                        on_event=_loop_on_event if on_event is not None else None,
                        cancel_event=cancel_event,
                        parent_span_ctx=step_parent,
                    )
                except Exception as e:
                    scratchpad.append(
                        f"step {step_id} ({executor}) raised {e}; try another."
                    )
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "thought": thought_in,
                            "executor": executor,
                            "error": _truncate(str(e), 500),
                        }
                    )
                    continue
                outcome = (
                    exec_result.step_results[0] if exec_result.step_results else None
                )
                if outcome is None:
                    scratchpad.append(f"step {step_id} produced nothing; try another.")
                    iter_obs.update(
                        output={
                            "status": "retry",
                            "thought": thought_in,
                            "executor": executor,
                        }
                    )
                    continue
                if is_tool and executor in _RETRIEVAL_EXECUTORS and outcome.output:
                    prior_step = seen_observations.get(outcome.output)
                    if prior_step is not None:
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "retrieval returned a result already seen",
                                    "executor": executor,
                                    "error": (
                                        f"{executor} already returned this exact "
                                        f"result at step {prior_step}"
                                    ),
                                }
                            )
                            break
                        scratchpad.append(
                            f"step {prior_step} ({executor}) already returned this "
                            f"EXACT result — re-querying with different words "
                            f"surfaced nothing new. Answer now with is_final=true "
                            f"using what you have (and say honestly if the "
                            f"documents do not contain what was asked)."
                        )
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "rejection_reason": "retrieval repeated an identical result",
                                "executor": executor,
                                "error": (
                                    f"repeat of the result already returned by "
                                    f"step {prior_step}"
                                ),
                            }
                        )
                        continue
                    seen_observations[outcome.output] = step_id
                steps.append(mini.steps[0])
                step_results.append(outcome)
                guard.record_progress()
                seen_actions.add(sig)
                thought = str(raw.get("thought", ""))[:300]
                if outcome.status is StepStatus.SUCCESS:
                    if is_tool and executor == "plot.chart":
                        data_key = _plot_data_key(action_input)
                        if data_key is not None:
                            plotted_data.add(data_key)
                        observation = _chart_observation(action_input)
                        chart_fingerprints[step_id] = observation
                    elif is_tool and executor == "code.read":
                        observation = (outcome.output or "")[
                            :_CODE_READ_SCRATCHPAD_LIMIT
                        ]
                    elif is_tool and executor in _RETRIEVAL_EXECUTORS:
                        observation = (outcome.output or "")[
                            :_RETRIEVAL_SCRATCHPAD_LIMIT
                        ]
                    else:
                        observation = (outcome.output or "")[:1500]
                    if not is_tool and _is_empty_file_claim(outcome.output):
                        if guard.record_idle():
                            iter_obs.update(
                                output={
                                    "status": "failed",
                                    "idle_guard": True,
                                    "idle_reason": "agent reported missing file content",
                                    "thought": thought_in,
                                    "executor": executor,
                                    "error": "agent reported missing file content",
                                }
                            )
                            break
                        scratchpad.append(
                            f"step {step_id} ({executor}) reported it has no file "
                            "content — that step fetched nothing. Call code.read "
                            "with a literal file_id from notebook.inspect FIRST, "
                            "then pass its content into the coding message."
                        )
                        iter_obs.update(
                            output={
                                "status": "retry",
                                "thought": thought_in,
                                "executor": executor,
                                "error": "agent reported missing file content",
                            }
                        )
                        continue
                    scratchpad.append(
                        f"step {step_id} ({executor}) thought: {thought} "
                        f"observation: {observation}"
                    )
                    iter_obs.update(
                        output={
                            "status": "success",
                            "thought": thought_in,
                            "executor": executor,
                            "observation": _truncate(observation, 2000),
                        }
                    )
                    if is_tool and executor == "plot.chart":
                        plot_successes = sum(
                            1 for r in step_results
                            if r.agent_id == "plot.chart" and r.status is StepStatus.SUCCESS
                        )
                        if plot_successes >= MAX_PLOT_CHARTS_PER_RUN:
                            scratchpad.append(
                                f"plot.chart has succeeded {plot_successes} times — "
                                "answer from what you have, do not plot more charts."
                            )
                            break
                    if is_tool and executor == "doc.generate":
                        content_key = _doc_content_key(action_input)
                        if content_key is not None:
                            seen_doc_content.add(content_key)
                        gen_target = str(
                            action_input.get("target_format") or "md"
                        ).strip().lower()
                        if gen_target in ("md", "docx", "pdf"):
                            generated_formats.add(gen_target)
                        scratchpad.append(
                            "document generated successfully — use it as the final "
                            "answer, do not run doc.generate again. The "
                            f"{gen_target} file already exists — never run "
                            "doc.convert for it (doc.convert only re-renders "
                            "ORIGINAL uploads); answer now with is_final=true."
                        )
                    if is_tool and executor == "doc.convert":
                        consecutive_reasoning = 0
                        scratchpad.append(
                            "document converted successfully — use it as the final "
                            "answer, do not run doc.convert again and do not "
                            "rebuild the same content with doc.generate either."
                        )
                    if is_tool and executor == "doc.generate":
                        consecutive_reasoning = 0
                    elif not is_tool:
                        consecutive_reasoning += 1
                        has_doc = any(
                            r.agent_id == "doc.generate"
                            and r.status is StepStatus.SUCCESS
                            for r in step_results
                        )
                        if (
                            corpus_empty
                            and doc_wanted
                            and not has_doc
                        ):
                            scratchpad.append(
                                "draft captured — NEXT STEP MUST be doc.generate with "
                                '{"title", "sections": [{"heading", "body": full draft verbatim}], '
                                '"target_format": pdf/docx/md from the request}. '
                                "Do not draft again with reasoning."
                            )
                            if consecutive_reasoning >= 2 and guard.record_idle():
                                iter_obs.update(
                                    output={
                                        "status": "failed",
                                        "idle_guard": True,
                                        "idle_reason": "repeated reasoning without doc.generate",
                                        "error": "repeated drafts without producing the document",
                                    }
                                )
                                break
                    else:
                        consecutive_reasoning = 0
                else:
                    consecutive_reasoning = 0
                    failed_actions[sig] = outcome.error or "unknown error"
                    scratchpad.append(
                        f"step {step_id} ({executor}) failed: {outcome.error}; try another."
                    )
                    iter_obs.update(
                        output={
                            "status": outcome.status.value,
                            "thought": thought_in,
                            "executor": executor,
                            "error": _truncate(outcome.error, 500),
                        }
                    )
        has_terminal_doc = any(
            r.agent_id == "doc.generate" and r.status is StepStatus.SUCCESS
            for r in step_results
        )
        all_ok = bool(step_results) and all(
            r.status is StepStatus.SUCCESS for r in step_results
        )
        synth = None
        cancelled = cancel_event is not None and cancel_event.is_set()
        if not cancelled and not (has_terminal_doc and all_ok):
            synth = self._synth_fn(
                request_text,
                step_results,
                context=context,
                notebook_id=notebook_id,
                timeout_ms=timeout_ms,
                cancel_event=cancel_event,
                on_event=on_event,
                chart_fingerprints=chart_fingerprints,
                hint_answer=final_hint_answer or None,
            )
        if synth is not None:
            synth_step, synth_outcome = synth
            steps.append(synth_step)
            step_results.append(synth_outcome)
        elif final_hint_answer and not cancelled:
            steps.append(
                PlanStep(
                    step_id="r-final",
                    agent_id="reasoning",
                    input={"message": final_hint_answer},
                    expected_output_type="answer",
                )
            )
            step_results.append(
                StepResult(
                    step_id="r-final",
                    agent_id="reasoning",
                    status=StepStatus.SUCCESS,
                    output=final_hint_answer,
                )
            )
        plan = Plan(
            plan_id=str(uuid.uuid4()),
            goal=request_text,
            steps=steps
            or [
                PlanStep(
                    step_id="r0",
                    agent_id="reasoning",
                    input={"message": request_text},
                    expected_output_type="text",
                )
            ],
        )
        if corpus_processing and not step_results:
            wait_text = (
                "The document is still being processed, so there are no "
                "chunks to read yet and I cannot answer this request. "
                "Please wait until processing finishes, then send the "
                "request again — the content will be available."
            )
            steps.append(
                PlanStep(
                    step_id="r0",
                    agent_id="reasoning",
                    input={"message": wait_text},
                    expected_output_type="clarification",
                )
            )
            step_results.append(
                StepResult(
                    step_id="r0",
                    agent_id="reasoning",
                    status=StepStatus.SUCCESS,
                    output=wait_text,
                )
            )
            return ReactResult(
                Plan(
                    plan_id=str(uuid.uuid4()),
                    goal=request_text,
                    steps=steps,
                ),
                ExecutionResult(
                    trace_id=self._trace_id, step_results=step_results
                ),
            )
        if not step_results:
            step_results = [
                StepResult(
                    step_id="r0",
                    agent_id="react",
                    status=StepStatus.FAILURE,
                    error=(
                        f"{no_step_reason or 'no usable action was found'}. "
                        "Try naming the file explicitly, or restating what to "
                        "produce (for example: 'write an HTML page that charts "
                        "these numbers' or 'summarize the documents')."
                    ),
                )
            ]
        return ReactResult(
            plan, ExecutionResult(trace_id=self._trace_id, step_results=step_results)
        )