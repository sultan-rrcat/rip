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

    @property
    def provider(self) -> ModelProvider:
        """Expose the planning provider for layered planning (router reuse)."""
        return self._provider

    def plan(
        self,
        request_text: str,
        context: str | None = None,
        notebook_context: str | None = None,
        feedback: str | None = None,
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
          {{"query": "<search terms>", "top_k": 4, "file_id": "<optional literal snapshot id>",
          "mode": "specific|overview"}}. NEVER include
          notebook_id in any step input — it is injected at execution time.
          Use rag.query for question-answering, summaries and reports
          grounded in document content — NEVER for verbatim file conversion.
          file_id must be a literal snapshot id (same rule as doc.convert) —
          NEVER invented, NEVER a {{{{...}}}} placeholder. mode=specific
          (default) is topical ranking; mode=overview is a stratified
          one-per-section sample for summarize/overall-theme asks. Compare /
          summarize / quiz with >=2 ready files → ONE file-scoped rag.query
          PER FILE (top_k=4 each, specific for compare-rank, overview for
          summarize/quiz) fanning into one reasoning step — NEVER two
          generic global queries (they collapse to one dominant document).
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
           or tool_id (deterministic utility work) as TOP-LEVEL step keys —
           NEVER nested inside "input". Never set both, never
           neither. Use ONLY the listed agent_ids and tool_ids.
           The steps array holds ONLY step objects: NEVER a bare string
           element like "step_id": "3", and NEVER a whole step (with its own
           step_id/depends_on/expected_output_type) buried inside another
           step's "input" — each step is its own TOP-LEVEL steps[] element.
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
           - "input" holds ONLY content fields (message, query, ...).
             Wiring keys (depends_on, expected_output_type) live at step
             TOP level: misplaced copies are auto-hoisted, conflicting
             duplicates are rejected.
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
           or 2. A dependent plot with literal values and no placeholder
           is rejected by the validator (fail-honest, no fake chart).
           Because placeholders cannot address structured data,
           NEVER chain notebook.inspect into doc.convert (or any tool
           needing a file id): use literal snapshot ids or "*" instead
           (rule 8). Standalone plots with user-given literal numbers
           (no depends_on) stay legal.
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
              → rag.query FIRST (query=<search terms>, top_k=4,
             expected_output_type="chunks" — mandatory, never "text":
             the validator rejects rag.query typed otherwise because raw
             chunks would leak into the answer), then a
             reasoning step answering ONLY from {{1}} chunks. Say "not
             in the documents" when chunks are empty. NEVER answer from
             parametric knowledge when ready documents exist. Generating
             questions/quiz/MCQs from documents is content work like any
             QA → rag.query + reasoning, NEVER doc.convert (verbatim file
             conversion only, no LLM question-writing).
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
             doc.generate with depends_on must reference an upstream
             answer/summary step (validator-enforced).
           - "Summarize + plot" (e.g. "summarize the class distribution
             and show me the plot") → FOUR steps, never three: rag.query
             chunks, then numbers-only reasoning + answer reasoning IN
             PARALLEL off the chunks, then plot.chart depending ONLY on
             the numbers step (values [{{numbers-step}}], concrete
              labels from the chunks — never "Class 0/1"). One reasoning
              step can NEVER feed both prose and plot values because a
              placeholder carries whole text the plot cannot parse as
              numbers (see Example E). CRITICAL: BOTH the numbers step
              AND the answer step MUST contain {{{{1}}}} in their message
              (e.g. "Extract ... from {{{{1}}}}") — a dependent step with
              no placeholder is rejected and the run fails.
           - Comparison plots (A vs B, one value per label): the numbers
             step MUST return exactly one number per label, in label
             order, as a flat comma-separated list with no words.
             Multiple sources fan into ONE merging numbers-only step
             (e.g. "combine {{2}} and {{4}} into one comma-separated
             list, one value per label") — plot.chart depends ONLY on
             the merge step with values [{{merge-step}}]. NEVER put two
             placeholders (",{{2}}", ",{{4}}") or text around a
             placeholder inside values: each values element is a number
             or a lone {{id}} (validator-enforced).
           - Ambiguous convert (singular "convert it / the document" with
             several ready files, or no target format stated, or the file
             is still processing) → return EXACTLY ONE reasoning step
             whose message asks the counter-question (e.g. "Which document
             should I convert, and to which format: md, docx or pdf?").
             The aggregator returns it verbatim as a clarification
             question.
        9. PARALLELISM BUDGET (local single-model backend): steps with no
           depends_on run AT THE SAME TIME against one Ollama server.
           NEVER fan out more than 5 long-text reasoning steps in parallel
           off the same parent (the validator rejects 6+; the current
           model/host sustains at least 5 concurrent writes). MCQ/quiz
           generation is PREFERABLY exactly TWO steps: rag.query FIRST,
           then ONE reasoning step writing ALL questions (e.g. all 15:
           5 beginner + 5 intermediate + 5 senior) from {{{{1}}}} chunks;
           splitting by difficulty level (up to 5 parallel writers) is
           allowed but costs more. Beyond 5 parallel long writes, chain
           SEQUENTIALLY instead (2 depends_on ["1"], 3 depends_on ["2"],
           ...) so each gets the model to itself. Short branches (numbers
           + answer in Example E) and cheap deterministic tools may stay
           parallel.
           Every agent step with depends_on MUST embed a {{{{id}}}}
           placeholder referencing an upstream step in its message —
           plans without one are rejected as ungrounded.

        Examples:
        Example A (document question answering):
        {{"goal": "Answer what the documents say about X", "steps": [
            {{"step_id": "1", "tool_id": "rag.query",
              "input": {{"query": "X", "top_k": 4}},
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

        Example E (document summary + plot — split numbers and answer):
        {{"goal": "Summarize the class distribution and plot it", "steps": [
            {{"step_id": "1", "tool_id": "rag.query",
              "input": {{"query": "class distribution", "top_k": 4}},
              "depends_on": [], "expected_output_type": "chunks"}},
            {{"step_id": "2", "agent_id": "reasoning",
              "input": {{"message": "Extract the class counts for charting from {{{{1}}}}. "
                        "Return ONLY the raw numbers, comma-separated, no words."}},
              "depends_on": ["1"], "expected_output_type": "numbers"}},
            {{"step_id": "3", "agent_id": "reasoning",
              "input": {{"message": "Summarize the class distribution using {{{{1}}}}. "
                        "Return ONLY the summary prose: no plot talk, no code fences, "
                        "no follow-up questions."}},
              "depends_on": ["1"], "expected_output_type": "answer"}},
            {{"step_id": "4", "tool_id": "plot.chart",
              "input": {{"chart_type": "bar", "labels": ["Fire", "Smoke"],
                         "values": [{{{{2}}}}], "title": "Class distribution"}},
              "depends_on": ["2"], "expected_output_type": "chart"}}
        ]}}

        Example F (MCQ/quiz generation — ONE writer step, never fan-out):
        {{"goal": "Create 15 MCQs (5 beginner, 5 intermediate, 5 senior)", "steps": [
            {{"step_id": "1", "tool_id": "rag.query",
              "input": {{"query": "key concepts topics important details", "top_k": 4}},
              "depends_on": [], "expected_output_type": "chunks"}},
            {{"step_id": "2", "agent_id": "reasoning",
              "input": {{"message": "Using ONLY these retrieved chunks {{{{1}}}}, "
                        "write 15 MCQs (5 beginner, 5 intermediate, 5 senior), "
                        "each with 4 options and the correct answer marked."}},
              "depends_on": ["1"], "expected_output_type": "answer"}}
        ]}}
        """

        if feedback:
            # Bounded recall: a rejected plan or failed execution, with the
            # instance-specific error. Short and recent on purpose — it must
            # cut through the rules above, not restate them.
            system_prompt += f"\n\nRETRY FEEDBACK (previous attempt failed):\n{feedback}"

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
