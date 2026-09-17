"""L4 ReAct fallback — last resort when the mega-prompt fails twice.

Unlike upfront DAG planning, ReAct interleaves thought → action → observation:
each iteration proposes exactly ONE step, executes it immediately, and appends
the observation to the scratchpad. No placeholder wiring is ever emitted, so
the ecd93eb4 ungrounded-fan-in class cannot occur — inputs are inlined.

Bounded: max 6 iterations, cooperative cancel, per-step timeouts inherited
from run_plan_graph. Returns a (Plan, ExecutionResult) pair so the standard
deterministic Aggregator stays the single answer-assembly path.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

from app.agents.base import StepStatus
from app.agents.registry import AgentRegistry
from app.core.config import settings
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


def _output_type(executor: str, is_final: bool) -> str:
    if is_final:
        return "answer"
    return _TOOL_OUTPUT_TYPES.get(executor, "text")


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
) -> ReactResult:
    """Run the thought → action → observation loop to answer request_text."""
    known_agents = {a["agent_id"] for a in agents.manifest()}
    known_tools = {t["tool_id"] for t in tools.manifest()}
    model = settings.ollama_default_model
    agent_ids = sorted(known_agents)
    tool_ids = sorted(known_tools)

    scratchpad: list[str] = []
    steps: list[PlanStep] = []
    step_results: list[StepResult] = []
    # Consecutive turns that produced no observation (provider errors,
    # unknown executors, empty answers). Caps garbage-loops against a
    # degraded model; any executed step or final answer resets it.
    idle_turns = 0

    for iteration in range(1, max_iterations + 1):
        if cancel_event is not None and cancel_event.is_set():
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
            "step), input (agent: {\"message\": ...} with observations inlined "
            "verbatim — never reference steps by number; tool: its schema "
            "fields), is_final (true only when answering now), and answer "
            "(the final answer when is_final).\n"
            "Prefer rag.query first when documents are available. "
            f"Notebook documents:\n{notebook_context or '(no documents)'}"
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
                step_results.append(
                    StepResult(
                        step_id=f"r{iteration}", agent_id="react",
                        status=StepStatus.FAILURE,
                        error=f"react planner unavailable: {e}",
                    )
                )
                break
            scratchpad.append(f"planner error: {e}; propose a simpler next step.")
            continue
        executor = str(raw.get("executor", "")).strip()
        is_final = bool(raw.get("is_final", False))
        if is_final:
            answer = raw.get("answer") or ""
            if not str(answer).strip():
                idle_turns += 1
                if idle_turns >= 2:
                    break
                scratchpad.append("is_final was true but answer was empty; retry.")
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
            break
        if executor not in known_agents and executor not in known_tools:
            idle_turns += 1
            if idle_turns >= 2:
                break
            scratchpad.append(
                f"unknown executor {executor!r}; use one of {agent_ids + tool_ids}."
            )
            continue
        raw_input = raw.get("input", {}) or {}
        action_input = dict(raw_input) if isinstance(raw_input, dict) else {}
        if executor in known_agents and not str(action_input.get("message", "")).strip():
            idle_turns += 1
            if idle_turns >= 2:
                break
            scratchpad.append(f"agent {executor} needs input.message; retry with it.")
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
        try:
            exec_result = run_plan_graph(
                mini, agents, tool_registry=tools, trace_id=trace_id,
                notebook_id=notebook_id, context=None,
                fallback_message=request_text,
                timeout_ms=timeout_ms or settings.default_timeout_ms,
                on_event=on_event, cancel_event=cancel_event,
            )
        except Exception as e:  # noqa: BLE001 - execution error is an observation
            scratchpad.append(f"step {step_id} ({executor}) raised {e}; try another.")
            continue
        outcome = exec_result.step_results[0] if exec_result.step_results else None
        if outcome is None:
            scratchpad.append(f"step {step_id} produced nothing; try another.")
            continue
        steps.append(mini.steps[0])
        step_results.append(outcome)
        idle_turns = 0  # an executed step is progress, even on tool failure
        thought = str(raw.get("thought", ""))[:300]
        if outcome.status is StepStatus.SUCCESS:
            scratchpad.append(
                f"step {step_id} ({executor}) thought: {thought} "
                f"observation: {(outcome.output or '')[:1500]}"
            )
        else:
            scratchpad.append(
                f"step {step_id} ({executor}) failed: {outcome.error}; try another."
            )
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
