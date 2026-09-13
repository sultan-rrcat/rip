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

from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.orchestration.plan import Plan
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.validator")


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
