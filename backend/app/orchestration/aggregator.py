"""Aggregator — deterministic assembly of step outputs (Q36).

Turns a plan's per-step outputs into ONE final answer with NO LLM synthesis
step (merge decision: cuts cost and latency; predictable and testable).

Q36 locked rules:
- exactly one successful step → its output verbatim;
- multiple successes → labeled concatenation ("Step <id> (<executor>): ...");
- a step needing clarification → its question verbatim (never mangled);
- all steps failed (or nothing executed) → the joined error strings.

`conflicts` is always empty (no LLM to detect contradictions).
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.agents.base import StepStatus
from app.orchestration.plan import Plan
from app.orchestration.results import ExecutionResult

logger = logging.getLogger("orchestration.aggregator")


class AggregationResult(BaseModel):
    status: str  # "success" | "partial" | "failed"
    plan_incomplete: bool
    summary: str
    conflicts: list[str] = Field(default_factory=list)
    needs_clarification: bool = False


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
                status="failed", plan_incomplete=True, summary=summary
            )

        # One success: its output IS the answer.
        if len(successful) == 1:
            summary = successful[0].output or ""
        # Multiple successes: labeled concatenation.
        else:
            summary = "\n\n".join(
                f"Step {r.step_id} ({r.agent_id}): {r.output or ''}"
                for r in successful
            )
        logger.info(
            "aggregated plan=%s status=%s successes=%d",
            plan.plan_id, status, len(successful),
        )
        return AggregationResult(
            status=status,
            plan_incomplete=plan_incomplete,
            summary=summary,
        )
