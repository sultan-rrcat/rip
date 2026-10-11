"""Aggregator — deterministic assembly of step outputs (Q36).

Turns a plan's per-step outputs into ONE final answer with NO LLM synthesis
step (merge decision: cuts cost and latency; predictable and testable).

Type-aware deterministic rules (ADR-023 as amended):
- HIDE intermediate outputs (`expected_output_type` in chunks/numbers, or
  the evidence-gathering executors `notebook.inspect` / `code.read` /
  `code.sandbox`) unless they are the only output
  (anti-blank fallback);
- SHOW terminal outputs (answer/summary/text/document/chart/clarification,
  plus any unknown type — fail-visible, never fail-blank); chart/SVG
  outputs aggregate to a short placeholder (the SVG bytes travel via the
  SSE `artifacts` event as a download URL and render inline as <img>);
- single SHOW → verbatim (no `Step N` prefix, preserves chat tone);
  multi-SHOW → labeled concatenation (`Step <id> (<executor>): ...`,
  same placeholder per chart step);
- a step needing clarification → its question verbatim (never mangled);
- all steps failed (or nothing executed) → the joined error strings;
- partial runs append every failure line so successes never mask failures.
- `shown` / `hidden` / `visibility` expose the SHOW/HIDE decision per step
  (answer bubble = SHOW only; Steps panel = all, collapsed; Langfuse map).

`conflicts` is always empty (no LLM to detect contradictions).
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app.agents.base import StepStatus
from app.orchestration.plan import Plan
from app.orchestration.results import ExecutionResult, StepResult

logger = logging.getLogger("orchestration.aggregator")

#: Unresolved {{id}} placeholders must never reach the user (trace cfbaa9c3:
#: a reasoning step answered "I don't have access to {{1}} and {{2}}...").
from app.orchestration.placeholder import has_unresolved_placeholder

#: Placeholder replacing raw chart SVG in the user-visible summary. The SVG
#: bytes stay on StepResult.output (placeholder resolution, traces) and are
#: delivered as a file via the SSE `artifacts` event; the summary (and the
#: persisted assistant message) carries only this short text so the chat
#: never renders raw `<svg>` markup.
CHART_PLACEHOLDER = "Chart generated — see Artifacts below."

#: Placeholder replacing raw quiz JSON in the user-visible summary. The
#: quiz payload travels via StepResult.data → SSE `artifacts` as a .quiz.json
#: file and renders inline as an interactive test; the summary (and the
#: persisted assistant message) carries only this short text.
QUIZ_PLACEHOLDER = "Quiz ready — take the test below."


def _summarizable(output: str | None) -> str:
    """Return placeholder when output carries chart SVG, else verbatim."""
    text = output or ""
    if "<svg" in text.lower():
        return CHART_PLACEHOLDER
    # quiz.generate emits a short human summary as output, but ReAct or a
    # legacy path may leak the raw quiz JSON into a terminal step — never
    # render multi-KB JSON in chat; the interactive test renders via artifact.
    stripped = text.strip()
    if stripped.startswith("{") and '"questions"' in stripped:
        return QUIZ_PLACEHOLDER
    return text


#: Exploration-narration markers: a coding *report* over a sandbox
#: observation must describe completed execution, never narrate future
#: exploration (trace f6eaa575: the report step ignored the box output and
#: emitted "I'll explore the working directory … ls -la", which rendered
#: verbatim as the answer).
_DEGENERATE_REPORT_MARKERS = (
    "i'll explore",
    "i will explore",
    "let me explore",
    "let me look",
    "working directory to find",
    "ls -la",
)

#: Words proving a report actually references executed output (evidence
#: gate against false positives, e.g. a legit report quoting an `ls`
#: command alongside the output it produced).
_EXECUTION_EVIDENCE_MARKERS = (
    "sandbox",
    "printed",
    "output",
    "exit",
    "stdout",
    "stderr",
    "executed",
    "changed",
)

#: Chat budget for a substituted sandbox observation (the tool already caps
#: its own output; this keeps the bubble readable).
_SANDBOX_SUBSTITUTE_MAX_CHARS = 4000


def _is_degenerate_report(output: str | None) -> bool:
    """True when a coding report narrates exploration instead of results."""
    text = (output or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if not any(m in lowered for m in _DEGENERATE_REPORT_MARKERS):
        return False
    return not any(e in lowered for e in _EXECUTION_EVIDENCE_MARKERS)


class AggregationResult(BaseModel):
    status: str  # "success" | "partial" | "failed"
    plan_incomplete: bool
    summary: str
    conflicts: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    #: SHOW/HIDE decision per step_id ("show" | "hide"), in plan order terms.
    #: Drives the frontend answer bubble (SHOW only) vs the collapsed-all
    #: Steps panel, and the Langfuse aggregate-span visibility map.
    shown: list[str] = Field(default_factory=list)
    hidden: list[str] = Field(default_factory=list)
    visibility: dict[str, str] = Field(default_factory=dict)


class Aggregator:
    def aggregate(self, plan: Plan, result: ExecutionResult) -> AggregationResult:
        successful = [r for r in result.step_results if r.status is StepStatus.SUCCESS]
        failed = [r for r in result.step_results if r.status is StepStatus.FAILURE]
        if not result.step_results:
            return AggregationResult(
                status="failed",
                plan_incomplete=True,
                summary="No steps were executed.",
            )

        if len(successful) == len(result.step_results):
            status = "success"
        elif successful:
            status = "partial"
        else:
            status = "failed"

        plan_incomplete = not result.succeeded

        # A step asking for clarification returns its question verbatim —
        # synthesis would mangle the question.
        clarification = next(
            (r for r in result.step_results if r.needs_clarification), None
        )
        if clarification is not None:
            return AggregationResult(
                status="success",
                plan_incomplete=False,
                summary=clarification.output or "",
                needs_clarification=True,
                shown=[clarification.step_id],
                hidden=[r.step_id for r in result.step_results
                        if r.step_id != clarification.step_id],
                visibility={r.step_id: ("show" if r.step_id == clarification.step_id else "hide")
                            for r in result.step_results},
            )

        # All failed: join the error strings honestly.
        if status == "failed":
            summary = "; ".join(
                f"Step {r.step_id} ({r.agent_id}) failed: {r.error}" for r in failed
            )
            logger.info(
                "aggregated plan=%s status=failed failures=%d",
                plan.plan_id, len(failed),
            )
            return AggregationResult(
                status="failed", plan_incomplete=True, summary=summary,
                shown=[],
                hidden=[r.step_id for r in result.step_results],
                visibility={r.step_id: "hide" for r in result.step_results},
            )

        # Type-aware assembly: hide intermediates, show terminals in plan
        # order. Step internals remain available via step_completed events
        # for the UI collapsible; the summary is the persisted answer.
        step_meta = {s.step_id: s for s in plan.steps} if plan.steps else {}
        ordered = (
            [next((r for r in result.step_results if r.step_id == s.step_id), None)
             for s in plan.steps]
            if plan.steps else list(result.step_results)
        )
        ordered_successful = [r for r in ordered if r is not None and r.status is StepStatus.SUCCESS]

        def _hidden(step_id: str) -> bool:
            meta = step_meta.get(step_id)
            if meta is None:
                return False  # unknown step: fail-visible, never fail-blank
            eot = (meta.expected_output_type or "text").lower()
            if eot in ("chunks", "numbers", "quiz_json"):
                return True
            # Evidence-gathering steps: the terminal synthesis carries the
            # answer; raw stdout/listings stay in the Steps panel. The
            # code.sandbox observation is intermediate evidence for the
            # coding report step (ADR-050 L2 execute DAG).
            return meta.executor_id in ("notebook.inspect", "code.read", "code.sandbox")

        shown = [r for r in ordered_successful if not _hidden(r.step_id)]
        # ReAct runs build the answer incrementally (r1..rN, then r-final
        # synthesis or early is_final). The final answer already incorporates
        # prior observations; prior document/chart terminals travel via the
        # SSE artifacts event and the collapsed Steps panel. Concatenating
        # them into the main bubble duplicates large tables (trace d86f27e9:
        # r1 table + r2 convert msg + r-final all rendered in chat).
        # L2 builder plans use numeric step_ids ("1","2") and keep the
        # existing multi-SHOW behavior so chart placeholders aren't dropped.
        if step_meta and all(
            re.fullmatch(r"r\d+(?:_\d+)?|r-final|r0", sid or "") for sid in step_meta
        ):
            last = ordered_successful[-1] if ordered_successful else None
            if last is not None:
                last_meta = step_meta.get(last.step_id)
                last_eot = (
                    (last_meta.expected_output_type or "text").lower()
                    if last_meta else "text"
                )
                if last_eot in ("answer", "summary", "text", "document", "chart"):
                    shown_ids = {r.step_id for r in shown}
                    if last.step_id in shown_ids:
                        shown = [last]
        # Ungrounded-leak guard: a terminal output still carrying {{id}}
        # means placeholder resolution failed upstream (validator bypass or
        # pre-validation plan). Showing it leaks internals ("paste {{1}}");
        # demote to a failure line so the run retries honestly instead.
        leaked = [r for r in shown if has_unresolved_placeholder(r.output)]
        if leaked:
            shown = [r for r in shown if r not in leaked]
            for r in leaked:
                failed.append(
                    StepResult(
                        step_id=r.step_id,
                        agent_id=r.agent_id,
                        status=StepStatus.FAILURE,
                        error="ungrounded response (unresolved placeholders)",
                    )
                )
            # Recompute status: demoted terminals are failures now.
            effective_success = [r for r in ordered_successful if r not in leaked]
            if effective_success and failed:
                status = "partial"
            elif not effective_success:
                status = "failed"
            plan_incomplete = True
        # Duplicate-output guard: identical terminal outputs (trace 07fb4f59
        # plotted the same Avg-Tokens SVG twice, r2 then r3) would render
        # the same plot/text twice in chat. Keep the first, hide the rest —
        # the content is already visible, nothing is lost.
        if len(shown) > 1:
            seen_outputs: set[str] = set()
            deduped = []
            for r in shown:
                key = r.output or ""
                if key in seen_outputs:
                    continue
                seen_outputs.add(key)
                deduped.append(r)
            shown = deduped
        # Degenerate-report guard: the single shown terminal is a coding
        # report over a successful hidden sandbox step, but the report
        # narrates exploration instead of the box output (trace f6eaa575).
        # Substitute the trimmed sandbox observation — showing what the box
        # actually returned beats showing process narration. Narrowly scoped:
        # needs a hidden code.sandbox success with output to substitute.
        sandbox_src = next(
            (r for r in ordered_successful
             if _hidden(r.step_id) and (r.agent_id or "") == "code.sandbox"
             and (r.output or "").strip()),
            None,
        )
        if (
            sandbox_src is not None
            and len(shown) == 1
            and (shown[0].agent_id or "") == "coding"
            and _is_degenerate_report(shown[0].output)
        ):
            logger.warning(
                "degenerate coding report plan=%s step=%s; "
                "substituting sandbox observation",
                plan.plan_id, shown[0].step_id,
            )
            text = (sandbox_src.output or "").strip()
            if len(text) > _SANDBOX_SUBSTITUTE_MAX_CHARS:
                text = text[:_SANDBOX_SUBSTITUTE_MAX_CHARS].rstrip() + "\n[truncated]"
            shown = [sandbox_src.model_copy(update={"output": text})]
        if shown:
            if len(shown) == 1:
                summary = _summarizable(shown[0].output)
            else:
                summary = "\n\n".join(
                    f"Step {r.step_id} ({r.agent_id}): {_summarizable(r.output)}"
                    for r in shown
                )
        elif ordered_successful and not failed:
            # Anti-blank fallback: every success was intermediate (e.g. a
            # lone rag.query or numbers step) and nothing failed — surface
            # the last one. With failures present the errors below are the
            # honest answer; a hidden CSV dump would only add noise.
            summary = _summarizable(ordered_successful[-1].output)
        else:
            summary = ""

        # Partial runs must not hide failures behind successes (e.g. an
        # inspect listing masking failed converts) — append them honestly.
        if failed:
            failures = "\n".join(
                f"Step {r.step_id} ({r.agent_id}) failed: {r.error}" for r in failed
            )
            summary = f"{summary}\n\n{failures}" if summary.strip() else failures

        shown_ids = [r.step_id for r in shown]
        if not shown and ordered_successful and not failed:
            # Anti-blank fallback surfaces the last intermediate — it IS shown.
            shown_ids = [ordered_successful[-1].step_id]
        hidden_ids = [r.step_id for r in ordered_successful if r.step_id not in shown_ids]
        visibility = {r.step_id: ("hide" if r.step_id in hidden_ids else "show")
                      for r in ordered_successful}

        logger.info(
            "aggregated plan=%s status=%s successes=%d shown=%s hidden=%s",
            plan.plan_id, status, len(successful), shown_ids, hidden_ids,
        )
        return AggregationResult(
            status=status,
            plan_incomplete=plan_incomplete,
            summary=summary,
            shown=shown_ids,
            hidden=hidden_ids,
            visibility=visibility,
        )
