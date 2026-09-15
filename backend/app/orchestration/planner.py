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
# NOTE: no oneOf/anyOf here — Ollama 0.9.3 /api/chat `format` rejects them
# with 500 "invalid JSON schema in format" (2026-09-14, granite4.1:3b).
# agent_id/tool_id exactly-one-of is enforced by the Validator (fail-honest
# PlanValidationError) and by the system prompt rules, not by the schema.
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
                    "input": {"type": "object"},
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

    def plan(
        self,
        request_text: str,
        context: str | None = None,
        notebook_context: str | None = None,
    ) -> Plan:
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
          Use rag.query for question-answering, summaries and reports
          grounded in document content — NEVER for verbatim file conversion.
        - notebook.inspect lists the notebook's files
          (id, name, size, status). Its input is empty — notebook_id is
          injected at execution time like rag.query. It is a FRESHNESS
          PROBE ONLY: call it alone (single step) when the snapshot below
          says "(no documents)" but the user claims uploads exist. Its
          output can NEVER feed another step — placeholders only carry
          whole step-output text (rule 5), never file lists.
        - doc.generate renders a titled report from answer text
          (title, sections, tables). Use it for "as a report / in report
          format" requests, optionally after rag.query + reasoning.
          NEVER use it for file-to-file conversion.
        - doc.convert converts uploaded PDF/DOCX files to md, docx or pdf
          from the source (lossless, no LLM, no search). Its input is
          {{"file_id": "<literal id from the snapshot below, or \"*\" for
          every ready file>", "target_format": "md|docx|pdf"}} — NEVER
          include notebook_id. file_id must be a literal snapshot id or
          "*" — NEVER invented, NEVER a {{{{...}}}} placeholder (reasons
          in rule 5). Use it ONLY for convert/export/save-as requests.
        - plot.chart renders bar/line charts from explicit labels + values.
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
           replaces it with that step's WHOLE text output BEFORE the tool
           runs. Placeholders carry ONLY whole text: dotted/path forms like
           {{{{1.files[0].id}}}} or {{{{1.data}}}} DO NOT EXIST and must
           NEVER be emitted — a step's structured data (e.g. file lists)
           is invisible to later steps. When a tool step depends on upstream
           steps, use {{{{<step_id>}}}} for text values only.
           IMPORTANT: When a tool step (e.g. plot.chart) depends on upstream
           steps, you MUST use {{{{<step_id>}}}} placeholders for dynamic
           text values — NEVER hardcode placeholder numeric values like 1
           or 2. Because placeholders cannot address structured data,
           NEVER chain notebook.inspect into doc.convert (or any tool
           needing a file id): use literal snapshot ids or "*" instead
           (rule 8).
        6. If the request is trivial/conversational and needs no tools or
           multi-step work, return EXACTLY ONE step with agent_id="reasoning"
           and input={{"message": "<the user's request verbatim>"}}.
           Never return an empty steps array — empty plans cannot execute
           and always aggregate as failed.
           A factual question is NOT trivial when the snapshot below lists
           ready documents — it needs the document-grounded path (rule 8),
           not a bare reasoning step.
        7. Return only the JSON object matching the provided execution-plan schema.
        8. DOCUMENT ROUTING (snapshot below lists this notebook's files):
           - Factual/QA/summary/report-about-documents with >=1 ready file
             → rag.query FIRST (query=<search terms>, top_k=8), then a
             reasoning step answering ONLY from {{{{1}}}} chunks. Say "not
             in the documents" when chunks are empty. NEVER answer from
             parametric knowledge when ready documents exist.
           - Convert ALL / plural ("convert the uploaded documents / all
             files to pdf", no names) → EXACTLY ONE doc.convert step with
             {{"file_id": "*", "target_format": "<format>"}}. No inspect,
             no fan-out, no placeholders.
           - Convert ONE named file ("convert X to md") → EXACTLY ONE
             doc.convert step with the literal file_id from the snapshot
             and target_format. No inspect when the snapshot identifies it.
             NEVER put rag.query, reasoning content work, or doc.generate
             on any convert path — conversion is verbatim.
           - "As a report / in report format" → answer/synthesize first
             (rag.query + reasoning when document-grounded), then
             doc.generate with title/sections/tables. NEVER doc.convert.
           - Ambiguous convert (singular "convert it / the document" with
             several ready files, or no target format stated, or the file
             is still processing) → return EXACTLY ONE reasoning step
             whose message asks the counter-question (e.g. "Which document
             should I convert, and to which format: md, docx or pdf?").
             The aggregator returns it verbatim as a clarification
             question.

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

        Example C (single-file conversion — literal snapshot id, one step):
        {{"goal": "Convert a.pdf to markdown", "steps": [
            {{"step_id": "1", "tool_id": "doc.convert",
              "input": {{"file_id": "<literal id of a.pdf from the snapshot below>", "target_format": "md"}},
              "depends_on": [], "expected_output_type": "document"}}
        ]}}

        Example C2 (convert ALL — one step, file_id "*", no inspect):
        {{"goal": "Convert all uploaded documents to PDF", "steps": [
            {{"step_id": "1", "tool_id": "doc.convert",
              "input": {{"file_id": "*", "target_format": "pdf"}},
              "depends_on": [], "expected_output_type": "document"}}
        ]}}

        Example D (ambiguous convert → counter-question, exactly one step):
        {{"goal": "Ask which document and format to convert", "steps": [
            {{"step_id": "1", "agent_id": "reasoning",
              "input": {{"message": "Which document should I convert, and to which format: md, docx or pdf?"}},
              "depends_on": [], "expected_output_type": "clarification"}}
        ]}}
        """
        if context:
            system_prompt += f"\n\nConversation context:\n{context}"
        system_prompt += (
            "\n\nNotebook documents (snapshot; may be stale — call "
            "notebook.inspect live when ambiguous or a file is processing):\n"
            f"{notebook_context or '(no documents)'}"
        )

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
