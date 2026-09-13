"""Planner — turns a user request into an execution plan.

The Planner is the ONLY place an LLM is used for planning. It reads the
agent registry's manifest() and the tool registry's manifest() as its menu
and calls provider.generate_structured with a JSON schema so the plan comes
back as valid, parseable JSON.

RIP port: Ollama (`ollama_default_model`) instead of Gemini; prompts
reference the RIP tool set (rag.query, plot.chart, doc.generate,
code.sandbox, image.generate). The Planner NEVER emits `notebook_id` in step
inputs — the engine injects it from the run at execution time (Q6).
"""

from __future__ import annotations

import logging
import uuid

from app.agents.registry import AgentRegistry
from app.core.config import settings
from app.orchestration.plan import Plan
from app.providers.base import ModelProvider
from app.tools.registry import ToolRegistry

logger = logging.getLogger("orchestration.planner")

# JSON Schema describing the plan shape the model must produce. It must
# MATCH the Plan/PlanStep pydantic models above — same field names/types.
# agent_id and tool_id are exactly-one-of (enforced by the Validator):
# the schema keeps both optional so a malformed step surfaces as a
# validation failure, never a schema crash.
PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "tool_id": {"type": "string"},
                    # Allow the LLM to output arbitrary tool-specific keys
                    "input": {
                        "type": "object"
                    },
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "expected_output_type": {"type": "string"},
                },
                "required": ["step_id", "input"],
            },
        },
    },
    "required": ["goal", "steps"],
}


class Planner:
    def __init__(
        self,
        provider: ModelProvider,
        registry: AgentRegistry,
        tool_registry: ToolRegistry | None = None,
    ):
        self._provider = provider
        self._registry = registry
        self._tool_registry = tool_registry or ToolRegistry()
        self._model = settings.ollama_default_model

    def plan(self, request_text: str, context: str | None = None) -> Plan:
        manifest = self._registry.manifest()
        tool_manifest = self._tool_registry.manifest()
        system_prompt = f"""
        You are an execution-plan planner.

        Your goal is to convert the user's request into a JSON execution plan.

        Available agents:
        {manifest}

        Available tools (deterministic utilities — prefer these over agents
        for single-purpose work like document search and charting):
        {tool_manifest}

        Tool notes:
        - rag.query searches the user's notebook documents. Its input is
          {{"query": "<search terms>", "top_k": 8}}. NEVER include
          notebook_id in any step input — it is injected at execution time.
        - plot.chart renders bar/line charts from explicit labels + values.
        - doc.generate renders a titled report (title, sections, tables).
        - code.sandbox runs a Python snippet in a locked-down container.
        - image.generate makes one image from a text prompt.

        Rules:
        1. Every step sets EXACTLY ONE of agent_id (delegate reasoning work)
           or tool_id (deterministic utility work). Never set both, never
           neither. Use ONLY the listed agent_ids and tool_ids.
        2. Assign each step a unique step_id.
        3. Use depends_on to specify ordering and dependencies between steps.
           depends_on entries must be EXACT step_id values (e.g. "2"), never
           prefixed forms like "step_2".
        4. INPUT FIELDS BY STEP TYPE:
           - Agent steps (agent_id): include a "message" key with the sub-task
             description, written so the agent can act without seeing the whole
             request. Never leave "input" empty.
           - Tool steps (tool_id): use the tool's input_schema fields directly
             (e.g. query, top_k for rag.query; chart_type, labels, values,
             title for plot.chart). Do NOT put tool input inside a "message"
             key — the tool reads its own schema fields. Never leave "input"
             empty.
        5. To use an earlier step's result inside a later step, embed the
           placeholder {{{{<step_id>}}}} - two braces around the exact step_id,
           e.g. {{{{1}}}} for step "1" - in this step's input values. The engine
           replaces it with that step's output. Placeholders work in BOTH
           string fields (title, message) AND array/number fields (values).
           IMPORTANT: When a tool step (e.g. plot.chart) depends on upstream
           steps, you MUST use {{{{<step_id>}}}} placeholders for dynamic
           values — NEVER hardcode placeholder numeric values like 1 or 2.
           The engine resolves placeholders BEFORE the tool runs.
        6. If the request is trivial/conversational and needs no tools or
           multi-step work, return EXACTLY ONE step with agent_id="reasoning"
           and input={{"message": "<the user's request verbatim>"}}.
           Never return an empty steps array — empty plans cannot execute
           and always aggregate as failed.
        7. Return only the JSON object matching the provided execution-plan schema.

        Examples:
        Example A (document question answering):
        {{"goal": "Answer what the documents say about X", "steps": [
            {{"step_id": "1", "tool_id": "rag.query",
              "input": {{"query": "X", "top_k": 8}},
              "depends_on": [], "expected_output_type": "chunks"}},
            {{"step_id": "2", "agent_id": "reasoning",
              "input": {{"message": "Answer the user's question using these retrieved chunks: {{{{1}}}}"}},
              "depends_on": ["1"], "expected_output_type": "answer"}}
        ]}}

        Example B (agent steps + tool step with placeholders in values):
        {{"goal": "Plot quarterly revenue", "steps": [
            {{"step_id": "1", "agent_id": "reasoning",
              "input": {{"message": "Extract the four quarterly revenue figures. "
                        "Return ONLY the raw numbers, comma-separated, no words."}},
              "depends_on": [], "expected_output_type": "numbers"}},
            {{"step_id": "2", "tool_id": "plot.chart",
              "input": {{"chart_type": "bar", "labels": ["Q1", "Q2", "Q3", "Q4"],
                         "values": [{{{{1}}}}], "title": "Quarterly revenue"}},
              "depends_on": ["1"], "expected_output_type": "chart"}}
        ]}}
        CRITICAL: dynamic values use placeholders, NOT hardcoded numbers.
        The engine resolves {{{{1}}}} to step 1's output BEFORE passing to
        the tool.
        """
        if context:
            system_prompt += f"\n\nConversation context:\n{context}"

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": request_text,
            },
        ]

        result = self._provider.generate_structured(
            model=self._model,
            messages=messages,
            schema=PLAN_SCHEMA,
            temperature=0,
        )

        plan = Plan.from_model(
            plan_id=str(uuid.uuid4()),
            goal=result["goal"],
            raw_steps=result["steps"],
        )

        logger.info(
            "planned plan=%s steps=%d executors=%s",
            plan.plan_id,
            len(plan.steps),
            ", ".join(s.executor_id for s in plan.steps),
        )

        return plan
