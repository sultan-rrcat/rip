"""Execution-plan data model.

A Plan is a small DAG: ordered steps, each naming an agent, its inputs, and
its dependencies. The Planner emits it (structured output), the Validator
checks it, the Execution Engine runs it. These pydantic models ARE the
contract — keep them in one place.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field

logger = logging.getLogger("orchestration.plan")


class PlanStep(BaseModel):
    """One DAG node: EITHER an agent step (agent_id) OR a tool step (tool_id).

    Exactly-one-of is enforced by the Validator (fail-honest
    PlanValidationError), not by construction — so malformed planner output
    surfaces as a validation failure with a clear message, never a crash.
    """

    step_id: str
    agent_id: str = ""
    tool_id: str | None = None
    input: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_output_type: str = "text"

    @property
    def kind(self) -> str:
        return "tool" if self.tool_id else "agent"

    @property
    def executor_id(self) -> str:
        return self.tool_id or self.agent_id


def _hoist_nested_executor(step: dict, plan_id: str, step_id: str) -> None:
    """Hoist executor ids buried in `input` to top-level step fields.

    Mutates `step` in place. Only fires when the top level names NO
    executor (empty agent_id and empty/None tool_id) and `input` holds
    exactly one truthy string under "agent_id"/"tool_id" — that key is
    moved up and deleted from input (no tool/agent schema uses those keys,
    so the move is lossless). Anything else (both present, non-strings,
    top level already set) is left for the Validator to reject.
    """
    if step.get("agent_id") or step.get("tool_id"):
        return
    raw_input = step.get("input")
    if not isinstance(raw_input, dict):
        return
    nested_agent = raw_input.get("agent_id")
    nested_tool = raw_input.get("tool_id")
    has_agent = isinstance(nested_agent, str) and bool(nested_agent.strip())
    has_tool = isinstance(nested_tool, str) and bool(nested_tool.strip())
    if has_agent == has_tool:  # both or neither: not our repair shape
        return
    if has_agent:
        step["agent_id"] = nested_agent.strip()
    else:
        step["tool_id"] = nested_tool.strip()
    del raw_input["agent_id" if has_agent else "tool_id"]
    logger.info(
        "plan %s step %s: hoisted nested %s to top level",
        plan_id, step_id, "agent_id" if has_agent else "tool_id",
    )


class Plan(BaseModel):
    plan_id: str
    goal: str
    steps: list[PlanStep] = Field(default_factory=list)

    def is_trivial(self) -> bool:
        return len(self.steps) == 0

    @classmethod
    def from_model(cls, plan_id: str, goal: str, raw_steps: list[dict]) -> Plan:
        """Build a Plan from the Planner's raw JSON, deterministically fixing
        four common LLM slips:
          - executor ids nested inside `input` ({"input": {"tool_id": ...}})
            instead of top-level step fields: hoisted when exactly one is
            present and the top level has neither (observed live: the model
            buries tool_id/agent_id in input, validator then rejects the
            step — hoisting turns a fail-honest abort into an executable
            plan; both-present stays rejected);
          - duplicate step_ids: first occurrence keeps its id, later ones get
            a numeric suffix;
          - exact-duplicate steps (same agent_id + same input): the later copy
            is DROPPED so it never executes or costs money, and any depends_on
            reference to it is redirected to the kept twin;
          - prefixed dependency ids ("step_1" where the id is "1"): normalized
            to the real id when a trailing-number match exists — the same
            parse-time repair philosophy as the dedup above. Genuinely unknown
            references are left untouched for the Validator to reject.
        """
        raw_ids = {r.get("step_id") or "step" for r in raw_steps}

        def canon_dep(dep: str) -> str:
            if dep in raw_ids:
                return dep
            m = re.search(r"(\d+)$", dep)
            if m and m.group(1) in raw_ids:
                return m.group(1)
            return dep

        resolved: dict[str, str] = {}  # raw id -> kept id (renames AND drops)
        used: set[str] = set()
        seen: dict[tuple[str, str, str], str] = {}  # (agent_id, tool_id, input) -> kept step_id
        steps: list[PlanStep] = []

        for raw in raw_steps:
            old = raw.get("step_id") or "step"
            if old not in resolved:
                new = old
                while new in used:
                    new = f"{new}_x"
            else:
                n = 2
                new = f"{old}_{n}"
                while new in used or new in resolved.values():
                    n += 1
                    new = f"{old}_{n}"

            step = dict(raw)
            step["step_id"] = new
            if isinstance(step.get("input"), dict):
                step["input"] = dict(step["input"])  # hoist mutates; don't touch caller's dict
            _hoist_nested_executor(step, plan_id, new)
            step["depends_on"] = [
                resolved.get(canon_dep(d), canon_dep(d))
                for d in step.get("depends_on", [])
            ]
            candidate = PlanStep(**step)

            sig = (
                candidate.agent_id,
                candidate.tool_id or "",
                json.dumps(candidate.input, sort_keys=True),
            )
            twin = seen.get(sig)
            if twin is not None:
                resolved[old] = twin  # drop: redirect references to the twin
                continue
            resolved[old] = new
            used.add(new)
            seen[sig] = new
            steps.append(candidate)

        return cls(plan_id=plan_id, goal=goal, steps=steps)
