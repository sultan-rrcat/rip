"""Plan Validator — deterministic safety gate between the Planner and execution.

The Planner is an LLM; it can produce valid-looking JSON that is still wrong
(nonexistent agent/tool, dependency cycle, oversized plan). The Validator is
the trust boundary: PURE rules, NO LLM. It raises a typed ValidationError on
any failure so the caller can fall back safely (fail honest).

Side-effecting tool steps PASS validation by design: RIP is local
single-user, so tools execute directly with no approval gate.
"""
from __future__ import annotations

import logging
import re

from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.orchestration.plan import Plan
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.validator")

#: Placeholder references inside step inputs, e.g. "{{2}}". Same shape as
#: plan_graph._PLACEHOLDER (kept local: the validator must not import the
#: execution graph).
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_-]+)\s*\}\}")

#: Known expected_output_type vocabulary. "summary" is a legacy/planner
#: variant of "answer" (both are terminal prose). Unknown types are allowed
#: (forward-compatible — the aggregator treats them as SHOW) but logged.
_KNOWN_OUTPUT_TYPES = frozenset({
    "chunks", "answer", "numbers", "chart", "document", "text",
    "clarification", "summary",
})

#: Terminal prose types that can ground a doc.generate report.
_ANSWER_TYPES = frozenset({"answer", "summary", "text"})


class PlanValidationError(ValueError):
    """Raised when a plan fails validation. Message is the reason."""


class PlanValidator:
    def __init__(
        self,
        registry: AgentRegistry,
        tool_registry: ToolRegistry | None = None,
        *,
        max_steps: int | None = None,
    ):
        self._registry = registry
        self._tool_registry = tool_registry or ToolRegistry()
        self._max_steps = max_steps or settings.default_max_plan_steps

    def validate(self, plan: Plan) -> Plan:

        self._check_unique_step_ids(plan) # -> PlanValidationError on duplicate step_id
        self._check_executors_exist(plan) # -> PlanValidationError on unknown agent/tool
        self._check_no_cycles(plan)       # -> PlanValidationError on a cycle
        self._check_budget(plan)          # -> PlanValidationError if too many steps
        self._check_dataflow_grounding(plan)  # -> PlanValidationError on ungrounded plot/report
        logger.info("plan %s validated (%d steps)", plan.plan_id, len(plan.steps))
        return plan

    def _check_unique_step_ids(self, plan: Plan) -> None:
        ids = [s.step_id for s in plan.steps]
        if len(ids) != len(set(ids)):
            raise PlanValidationError("plan contains duplicate step_ids")

    def _check_executors_exist(self, plan: Plan) -> None:
        manifest = self._registry.manifest()
        known_agent = {agent["agent_id"] for agent in manifest}
        known_tool = {tool["tool_id"] for tool in self._tool_registry.manifest()}

        for step in plan.steps:
            has_agent = bool(step.agent_id)
            has_tool = bool(step.tool_id)
            if has_agent == has_tool:  # both or neither
                raise PlanValidationError(
                    f"step {step.step_id} must set exactly one of agent_id/tool_id"
                )
            if has_agent and step.agent_id not in known_agent:
                raise PlanValidationError(f"step {step.step_id} references unknown agent")
            if has_tool and step.tool_id not in known_tool:
                raise PlanValidationError(f"step {step.step_id} references unknown tool")

    def _check_no_cycles(self, plan: Plan) -> None:
        step_ids = set()
        for step in plan.steps:
            step_ids.add(step.step_id)

        adjacency = {}
        for step in plan.steps:
            adjacency[step.step_id] = set(step.depends_on)

        for step in plan.steps:
            for dependency in step.depends_on:
                if dependency not in step_ids:
                    raise PlanValidationError(
                        f"step {step.step_id} depends on non existent step {dependency}"
                    )

        visiting = set()
        visited = set()

        def dfs(step_id: str) -> None:
            if step_id in visiting:
                raise PlanValidationError("cycle detected")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in adjacency[step_id]:
                dfs(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            dfs(step_id)

    def _check_budget(self, plan: Plan) -> None:
        if len(plan.steps) > self._max_steps:
            raise PlanValidationError(f"plan has {len(plan.steps)}")

    def _check_dataflow_grounding(self, plan: Plan) -> None:
        """Reject ungrounded plot/report steps (fail-honest, no fake charts).

        - plot.chart with dependencies must reference upstream via a
          {{{id}}} placeholder in `values` (literals + deps = hallucinated
          chart). Standalone plots with literal numbers stay legal.
        - plot.chart `values` placeholders must resolve to a numbers-type
          step: prose/chunks cannot parse as floats at runtime. Direct
          dependence on rag.query chunks is rejected for the same reason.
        - doc.generate with dependencies must have an upstream
          answer/summary/text step; standalone reports with full sections
          stay legal.
        """
        by_id = {s.step_id: s for s in plan.steps}
        for step in plan.steps:
            eot = (step.expected_output_type or "text").lower()
            if eot not in _KNOWN_OUTPUT_TYPES:
                logger.warning(
                    "plan %s step %s has unknown expected_output_type %r",
                    plan.plan_id, step.step_id, step.expected_output_type,
                )

        def upstream(step_id: str) -> set[str]:
            seen: set[str] = set()
            stack = list(by_id[step_id].depends_on)
            while stack:
                dep = stack.pop()
                if dep in seen or dep not in by_id:
                    continue
                seen.add(dep)
                stack.extend(by_id[dep].depends_on)
            return seen

        def placeholders_in(value: object) -> set[str]:
            found: set[str] = set()
            if isinstance(value, str):
                found.update(_PLACEHOLDER.findall(value))
            elif isinstance(value, dict):
                for v in value.values():
                    found.update(placeholders_in(v))
            elif isinstance(value, list):
                for v in value:
                    found.update(placeholders_in(v))
            return found

        for step in plan.steps:
            if step.tool_id == "plot.chart":
                values = step.input.get("values") if isinstance(step.input, dict) else None
                refs = placeholders_in(values)
                if step.depends_on:
                    if not refs:
                        raise PlanValidationError(
                            f"step {step.step_id} (plot.chart) depends on "
                            f"{sorted(step.depends_on)} but its values carry no "
                            "{{{id}}} placeholder — dependent plots must reference "
                            "upstream numbers, never hardcoded literals"
                        )
                    if isinstance(values, list):
                        for v in values:
                            if isinstance(v, str) and _PLACEHOLDER.search(v) and not _PLACEHOLDER.fullmatch(v.strip()):
                                raise PlanValidationError(
                                    f"step {step.step_id} (plot.chart) values element "
                                    f"{v!r} mixes a placeholder with surrounding text — "
                                    "each values element must be a number or a lone "
                                    "{{{id}}} placeholder"
                                )
                    if len(refs) > 1:
                        raise PlanValidationError(
                            f"step {step.step_id} (plot.chart) values reference "
                            f"multiple upstream steps {sorted(refs)} — fan them into "
                            "ONE merging numbers step first, then reference only it"
                        )
                    for ref in refs:
                        target = by_id.get(ref)
                        if target is None:
                            continue  # unknown dep: _check_no_cycles already rejects
                        target_eot = (target.expected_output_type or "text").lower()
                        if target_eot != "numbers":
                            raise PlanValidationError(
                                f"step {step.step_id} (plot.chart) values reference "
                                f"step {ref} ({target_eot or 'text'}), but plot values "
                                "must reference a numbers-producing step"
                            )
                    direct = [by_id[d] for d in step.depends_on if d in by_id]
                    if any(
                        d.tool_id == "rag.query"
                        and (d.expected_output_type or "").lower() == "chunks"
                        for d in direct
                    ):
                        raise PlanValidationError(
                            f"step {step.step_id} (plot.chart) depends directly on "
                            "rag.query chunks — route through a numbers-producing "
                            "reasoning step instead"
                        )
            elif step.tool_id == "doc.generate":
                if step.depends_on:
                    ups = upstream(step.step_id)
                    if not any(
                        (by_id[u].expected_output_type or "text").lower() in _ANSWER_TYPES
                        for u in ups
                    ):
                        raise PlanValidationError(
                            f"step {step.step_id} (doc.generate) depends on "
                            f"{sorted(step.depends_on)} with no upstream "
                            "answer/summary/text step — reports must be grounded "
                            "in an answer step"
                        )
