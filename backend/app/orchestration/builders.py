"""L2 deterministic builders — code-built DAGs, no DAG-LLM.

For intents in DETERMINISTIC_INTENTS the plan shape is fixed; only slot
values (queries, request text) vary. Wiring (depends_on + {{id}}
placeholders) is set by construction, so the ecd93eb4 failure class
(prose mention of steps without placeholders) cannot occur.

Convert builders resolve literal file ids from the notebook
snapshot; anything unresolvable returns None → L3 ReAct.
summarize_plot IS built (ADR-041): overview shards → one numbers step
emitting strict {"labels", "values"} JSON → plot.chart 'data', so
labels/values pair by construction; extraction failure fails honestly
in the tool.
"""

from __future__ import annotations

import os
import re
import uuid

from app.core.config import settings
from app.orchestration.corpus import (
    _ready_code,
    _ready_data,
    _ready_files,
    _snapshot_files,
    get_corpus_state,
)
from app.orchestration.intents import Intent
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.router import RouterResult

_PER_FILE_TOP_K = 4

#: CODE builder bounds: code files are inlined verbatim into the single
#: coding step (no chunking, no retrieval). Caps keep one prompt inside
#: the Ollama window alongside memory + system prompt.
_CODE_MAX_FILES = 3
_CODE_MAX_BYTES_PER_FILE = 32 * 1024
_CODE_MAX_BYTES_TOTAL = 64 * 1024

#: Snapshot file_ids are DB UUIDs; the builder never trusts anything else
#: for a disk path (no path separators reach open()).
#: Review-like asks (read-only) reuse the shared default budget — only
#: generation (write/create/new test/page) needs the generous coding budget,
#: which is still bounded to finish inside the agent wall-clock.
_REVIEW_RE = re.compile(
    r"\b(review|explain|describe|summariz\w*|what\s+does|how\s+does|walk\s*through)\b",
    re.IGNORECASE,
)
#: Execute-signal verbs for CODE dispatch: run/test/execute asks go through
#: the sandbox DAG (build_code_execute); everything else (explain/review/
#: generate) stays on the generate-only builder. Review verbs win over
#: execute verbs ("review the test file" is a review). "build" is
#: deliberately absent: "build a landing page" is greenfield generation.
_EXECUTE_RE = re.compile(
    r"\b(run|runs|running|execute|executes|executing|execution|"
    r"test|tests|testing|pytest|unittest|lint|fix|repair)\b",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_NOTEBOOK_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

#: DATA (CSV) builder bounds: data files are staged read-only into the
#: sandbox for pandas compute (never embedded, never inlined whole — only
#: a header + sample rides the report prompt). Caps keep the prompt inside
#: the Ollama window and the staging bounded.
_DATA_MAX_FILES = 2
_DATA_MAX_HEAD_ROWS = 20
_DATA_MAX_HEAD_BYTES = 8 * 1024
_DATA_HINT_RE = re.compile(r"\.csv\b", re.IGNORECASE)
_DATA_SIGNAL_RE = re.compile(
    r"\b(csv|data|dataset|table|column|row|average|mean|median|correlation|"
    r"trend|distribution|group\s*by|plot|chart|graph)\b",
    re.IGNORECASE,
)

#: Map file extensions to Markdown code-fence language identifiers.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "jsx",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".m": "objectivec",
    ".sh": "bash",
    ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".json": "json",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
}

#: Presentation tail for every grounded writer step (qa_single, compare,
#: summarize, quiz). Grounding invariants ("ONLY chunks", honest-empty
#: answers, no placeholder leaks) come first; this only styles HOW the
#: honest answer reads in chat. Kept short — the reasoning system prompt
#: carries the full voice.
#: Seed prose for the admin-editable fragments below (see
#: app/core/promptstore.py). The live values are read per build via
#: _presentation_suffix() / _empty_answer_guidance() so admin edits apply
#: to the next run without a restart.
_PRESENTATION_SUFFIX_SEED = (
    " Present like a world-class assistant: lead with the direct answer, "
    "then supporting detail in clear Markdown (short headings, bullets, "
    "numbered steps, or a table when it helps)."
)

#: Chart-awareness note for the summarize_plot summary step (trace
#: 2018fba0): the summary runs parallel to plot.chart and is otherwise
#: chart-blind, so it emits a matplotlib how-to alongside an already-rendered
#: chart. Static sentence — no placeholder, no extra LLM call, no latency.
_SUMMARIZE_PLOT_CHART_NOTE = (
    "A chart of these numbers is generated separately and shown in "
    "Artifacts — do not emit plotting code (no matplotlib/seaborn snippets, "
    "no 'how to plot' steps); end with one line referencing the chart. "
)
#: Honest-empty-answer guidance spliced into every grounded writer step.
#: The old "Say 'not in the documents'" line produced bare fragments that
#: read as broken output (observed live: a bare "not in the documents"
#: bubble). The replacement keeps the grounding invariant — never invent
#: coverage — but demands complete, professional sentences: what was
#: searched, what is missing, one concrete next step.
_EMPTY_ANSWER_GUIDANCE_SEED = (
    "Ground every claim in the chunks; for anything they do not cover, "
    "say so honestly in complete sentences — never a bare fragment. "
    "If the chunks are empty or read '(no chunks retrieved)', say you "
    "searched the notebook's documents and found nothing covering the "
    "request, name the topic briefly, and suggest one concrete next step "
    "(rephrasing the question or uploading a relevant document). "
)


def _presentation_suffix() -> str:
    """Live presentation fragment (admin override or code seed)."""
    from app.core.promptstore import get_prompt

    return get_prompt("builders.presentation_suffix")


def _empty_answer_guidance() -> str:
    """Live empty-answer fragment (admin override or code seed)."""
    from app.core.promptstore import get_prompt

    return get_prompt("builders.empty_answer_guidance")



def build_qa_no_docs(request_text: str) -> Plan:
    """Answer a factual question with no retrievable documents.

    Empty-corpus path: emitting rag.query would provably return
    "(no chunks retrieved)" and force the grounded prompt into an
    honest-empty answer — useless for general-knowledge questions
    like "What is QLoRA?". Answer from general knowledge instead
    (verbatim request, no document-grounding wrapper).
    """
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                agent_id="reasoning",
                input={"message": request_text},
                expected_output_type="answer",
            )
        ],
    )


def build_no_docs_clarification(
    request_text: str,
    *,
    processing: bool = False,
    intent: Intent | None = None,
) -> Plan:
    """Ask the user to upload/wait — nothing exists to summarize/compare.

    expected_output_type="clarification" so the single terminal step is
    returned verbatim as the answer (no retrieval to ground anything else).
    The message names the requested deliverable so an empty-notebook
    summarize/compare/quiz/plot does not collapse to one generic line
    (e.g. "Read all the files and identify issues" with no files).
    """
    what = {
        Intent.SUMMARIZE: "summarize",
        Intent.COMPARE_MULTI: "compare",
        Intent.QUIZ: "quiz",
        Intent.SUMMARIZE_PLOT: "summarize and plot",
        Intent.QA_SINGLE: "answer",
        Intent.CONVERT_ONE: "convert",
        Intent.CONVERT_ALL: "convert",
    }.get(intent) if intent is not None else None
    if processing:
        detail = (
            "The notebook's files are not ready yet (still uploading, "
            "processing, or errored). Ask the user to wait until "
            "processing finishes and then retry"
            + (f" the {what} request" if what else "")
        )
    else:
        detail = (
            "There are no ready documents in this notebook. Ask the user "
            "to upload documents or clarify how to proceed without them"
            + (
                f" (they asked to {what} document content, so name that "
                "the notebook is empty)"
                if what and what not in ("answer", "convert")
                else ""
            )
        )
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                agent_id="reasoning",
                input={"message": f"{detail}. Request: {request_text}"},
                expected_output_type="clarification",
            )
        ],
    )


def build_chat(request_text: str) -> Plan:
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                agent_id="reasoning",
                # Greetings/small-talk need no headroom: capped low so a
                # chatty model cannot burn the shared default per greeting.
                input={"message": request_text, "max_tokens": settings.chat_max_tokens},
                expected_output_type="text",
            )
        ],
    )


def build_qa_single(query: str, request_text: str) -> Plan:
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                tool_id="rag.query",
                input={
                    "query": query or request_text,
                    "top_k": _PER_FILE_TOP_K,
                    "mode": "specific",
                    # Builders ground on full text: never filter our evidence.
                    "verbatim": True,
                },
                expected_output_type="chunks",
            ),
            PlanStep(
                step_id="2",
                agent_id="reasoning",
                input={
                    "message": (
                        "Answer the user's request using ONLY these retrieved "
                        "chunks {{1}}. "
                        + _empty_answer_guidance()
                        + "Never mention chunk ids or placeholders. "
                        f"Request: {request_text}{_presentation_suffix()}"
                    )
                },
                depends_on=["1"],
                expected_output_type="answer",
            ),
        ],
    )


def build_compare_multi(
    queries: list[str],
    request_text: str,
    notebook_context: str | None = None,
) -> Plan:
    """Per-file specific shards fanning into one reasoning step.

    File-aware (not query-angle) fan-out: one file-scoped rag.query
    (mode=specific, top_k=4) per ready snapshot file. Topical query uses
    the request text so complexity/signal sections rank, not intro
    keywords. Falls back to legacy query-angle steps when no snapshot.
    Grounding is structural: every rag step id appears in both depends_on
    and as a {{id}} placeholder in the reasoning message.
    """
    ready = _ready_files(notebook_context)[:5]
    steps: list[PlanStep] = []
    if ready:
        for i, (_name, fid) in enumerate(ready, start=1):
            steps.append(
                PlanStep(
                    step_id=str(i),
                    tool_id="rag.query",
                    input={
                        "query": request_text,
                        "top_k": _PER_FILE_TOP_K,
                        "file_id": fid,
                        "mode": "specific",
                        # Builders ground on full text: never filter our evidence.
                        "verbatim": True,
                    },
                    expected_output_type="chunks",
                )
            )
        # Single ready file still yields one shard + reduce (honest
        # single-doc answer rather than a padded duplicate query).
        if len(steps) == 1 and len(_snapshot_files(notebook_context)) <= 1:
            pass
    else:
        sources = [q for q in (queries or []) if q.strip()]
        while len(sources) < 2:
            sources.append(request_text)
        sources = sources[:5]  # parallelism budget: ≤5 siblings
        for i, q in enumerate(sources, start=1):
            steps.append(
                PlanStep(
                    step_id=str(i),
                    tool_id="rag.query",
                    input={"query": q, "top_k": _PER_FILE_TOP_K, "mode": "specific",
                       # Builders ground on full text: never filter our evidence.
                       "verbatim": True},
                    expected_output_type="chunks",
                )
            )
    dep_ids = [s.step_id for s in steps]
    refs = " ".join(f"{{{{{sid}}}}}" for sid in dep_ids)
    steps.append(
        PlanStep(
            step_id=str(len(steps) + 1),
            agent_id="reasoning",
            input={
                "message": (
                    f"Using ONLY these retrieved chunks ({refs}), address the "
                    f"request. "
                    + _empty_answer_guidance()
                    + f"Never mention chunk ids or placeholders. "
                    f"Request: {request_text}{_presentation_suffix()}"
                )
            },
            depends_on=dep_ids,
            expected_output_type="answer",
        )
    )
    return Plan(plan_id=str(uuid.uuid4()), goal=request_text, steps=steps)


def build_summarize(request_text: str, notebook_context: str | None = None) -> Plan:
    """Per-file overview shards fanning into one reduce step.

    Each shard uses mode=overview (stratified one-per-H1 sample, top_k=4)
    so every file contributes its overall idea. Single reduce step keeps
    the Ollama budget flat.
    """
    ready = _ready_files(notebook_context)[:5]
    steps: list[PlanStep] = []
    if ready:
        for i, (_name, fid) in enumerate(ready, start=1):
            steps.append(
                PlanStep(
                    step_id=str(i),
                    tool_id="rag.query",
                    input={
                        "query": request_text,
                        "top_k": _PER_FILE_TOP_K,
                        "file_id": fid,
                        "mode": "overview",
                        # Builders ground on full text: never filter our evidence.
                        "verbatim": True,
                    },
                    expected_output_type="chunks",
                )
            )
    else:
        steps.append(
            PlanStep(
                step_id="1",
                tool_id="rag.query",
                input={
                    "query": request_text,
                    "top_k": _PER_FILE_TOP_K,
                    "mode": "overview",
                    # Builders ground on full text: never filter our evidence.
                    "verbatim": True,
                },
                expected_output_type="chunks",
            )
        )
    dep_ids = [s.step_id for s in steps]
    refs = " ".join(f"{{{{{sid}}}}}" for sid in dep_ids)
    steps.append(
        PlanStep(
            step_id=str(len(steps) + 1),
            agent_id="reasoning",
            input={
                "message": (
                    f"Using ONLY these retrieved chunks ({refs}), write the "
                    f"requested summary. "
                    + _empty_answer_guidance()
                    + f"Never mention chunk ids or placeholders. "
                    f"Request: {request_text}{_presentation_suffix()}"
                )
            },
            depends_on=dep_ids,
            expected_output_type="answer",
        )
    )
    return Plan(plan_id=str(uuid.uuid4()), goal=request_text, steps=steps)


def build_summarize_plot(
    request_text: str, notebook_context: str | None = None
) -> Plan:
    """Per-file overview shards → JSON extraction step → one plot.chart.

    The chart literals live in the documents, not the message — so they
    must be extracted. ONE reasoning step (eot
    "numbers") emits a single strict JSON object {"labels", "values"};
    pairing/order/count match by construction and plot.chart parses it
    via its 'data' input. A second reasoning step reduces the same shards
    into the written summary, so the intent's "summarize AND plot" both
    deliverables land. Anything unparseable fails honestly inside
    plot.chart — never a mislabeled chart.
    """
    ready = _ready_files(notebook_context)[:5]
    steps: list[PlanStep] = []
    if ready:
        for i, (_name, fid) in enumerate(ready, start=1):
            steps.append(
                PlanStep(
                    step_id=str(i),
                    tool_id="rag.query",
                    input={
                        "query": request_text,
                        "top_k": _PER_FILE_TOP_K,
                        "file_id": fid,
                        "mode": "overview",
                        # Builders ground on full text: never filter our evidence.
                        "verbatim": True,
                    },
                    expected_output_type="chunks",
                )
            )
    else:
        steps.append(
            PlanStep(
                step_id="1",
                tool_id="rag.query",
                input={
                    "query": request_text,
                    "top_k": _PER_FILE_TOP_K,
                    "mode": "overview",
                    # Builders ground on full text: never filter our evidence.
                    "verbatim": True,
                },
                expected_output_type="chunks",
            )
        )
    dep_ids = [s.step_id for s in steps]
    refs = " ".join(f"{{{{{sid}}}}}" for sid in dep_ids)
    extract_id = str(len(steps) + 1)
    steps.append(
        PlanStep(
            step_id=extract_id,
            agent_id="reasoning",
            input={
                "message": (
                    f"Using ONLY these retrieved chunks ({refs}), extract the "
                    "numeric data that answers the request's chart ask. Output "
                    'EXACTLY one JSON object of the form {"labels": [...], '
                    '"values": [...]} and NOTHING else — no prose, no code '
                    'fences. "labels" and "values" must be the same length '
                    "and in the same order; every value a bare number; labels "
                    'short strings. If the chunks contain no numeric data, '
                    'output {"labels": [], "values": []}. '
                    f"Request: {request_text}"
                )
            },
            depends_on=dep_ids,
            expected_output_type="numbers",
        )
    )
    steps.append(
        PlanStep(
            step_id=str(len(steps) + 1),
            tool_id="plot.chart",
            input={
                "data": f"{{{{{extract_id}}}}}",
                "chart_type": _chart_type_for(request_text),
                "title": " ".join((request_text or "").split())[:160]
                or "Chart",
            },
            depends_on=[extract_id],
            expected_output_type="chart",
        )
    )
    steps.append(
        PlanStep(
            step_id=str(len(steps) + 1),
            agent_id="reasoning",
            input={
                "message": (
                    f"Using ONLY these retrieved chunks ({refs}), write the "
                    f"requested summary. "
                    + _SUMMARIZE_PLOT_CHART_NOTE
                    + _empty_answer_guidance()
                    + f"Never mention chunk ids or placeholders. "
                    f"Request: {request_text}{_presentation_suffix()}"
                )
            },
            depends_on=dep_ids,
            expected_output_type="answer",
        )
    )
    return Plan(plan_id=str(uuid.uuid4()), goal=request_text, steps=steps)


_QUIZ_COUNT_RE = re.compile(r"(\d{1,2})\s*(?:mcqs?|msqs?|questions?|items?|problems?)", re.IGNORECASE)
_QUIZ_TYPE_RE = re.compile(r"\b(msq|multiple[ -]select|multi[ -]select|multiple[ -]choice|mcq|true[ -]?false|tf)\b", re.IGNORECASE)


def parse_quiz_params(request_text: str) -> tuple[int, str]:
    """Deterministic quiz slots: (count, kind) from raw text (never LLM).

    Count: first N in "N questions/MCQs/MSQs", clamped 2..50, default 10.
    Kind: msq when the text says MSQ/multiple-select/multi-select,
    truefalse when it says true/false / TF, else mcq (mixed requests also
    land on mcq — the extraction prompt asks for a mix).
    """
    from app.tools.quiz_generate import (
        QUIZ_MAX_QUESTIONS,
        QUIZ_MAX_QUESTIONS_DEFAULT,
        QUIZ_MIN_QUESTIONS,
    )

    count = QUIZ_MAX_QUESTIONS_DEFAULT
    m = _QUIZ_COUNT_RE.search(request_text or "")
    if m:
        try:
            count = max(QUIZ_MIN_QUESTIONS, min(QUIZ_MAX_QUESTIONS, int(m.group(1))))
        except ValueError:
            pass
    lowered = (request_text or "").lower()
    if re.search(r"\bmsqs?\b|multiple[ -]select|multi[ -]select", lowered):
        kind = "msq"
    elif re.search(r"\btrue[ -]?false\b|\btf\b", lowered):
        kind = "truefalse"
    else:
        kind = "mcq"
    return count, kind


def _quiz_extraction_message(refs: str, request_text: str, count: int, kind: str, grounded: bool) -> str:
    ground = (
        f"Using ONLY these retrieved chunks ({refs}), "
        if grounded
        else "Using your general knowledge on the requested topic, "
    )
    type_line = {
        "mcq": 'each with "type": "mcq", exactly 4 options and exactly 1 answer',
        "msq": 'each with "type": "msq", exactly 4 options and 2-3 answers',
        "truefalse": 'each with "type": "truefalse", exactly 2 options ("True", "False") and exactly 1 answer',
    }[kind]
    return (
        f"{ground}write exactly {count} test questions covering the request. "
        'Output EXACTLY one JSON object of the form {"questions": '
        '[{"type": ..., "stem": ..., "options": [{"id": "o1", "text": ...}], '
        '"answers": ["o1"], "explanation": ...}]} and NOTHING else — no prose, '
        "no code fences. "
        f"Question rules: {type_line}; every option a non-empty string with a "
        'unique id ("o1".."o4"); every answer id must match one option; each '
        "question a one-sentence explanation. "
        + (_empty_answer_guidance() if grounded else "")
        + f"Request: {request_text}"
    )


def build_quiz(
    query: str,
    request_text: str,
    notebook_context: str | None = None,
    *,
    count: int = 10,
    kind: str = "mcq",
) -> Plan:
    """Per-file overview shards → ONE quiz_json extraction → quiz.generate.

    Single extractor (never fan-out): splitting by difficulty costs more
    and contends the single Ollama server. Overview mode gives breadth for
    question coverage. Pairing is by construction (one JSON object), the
    same pattern as summarize_plot's numbers step (ADR-041).
    """

    def _rag_step(sid: str, **kw: object) -> PlanStep:
        base: dict = {
            "query": query or request_text,
            "top_k": _PER_FILE_TOP_K,
            "mode": "overview",
            # Builders ground on full text: never filter our evidence.
            "verbatim": True,
        }
        base.update(kw)
        return PlanStep(
            step_id=sid, tool_id="rag.query", input=base,
            expected_output_type="chunks",
        )

    ready = _ready_files(notebook_context)[:5]
    steps: list[PlanStep] = []
    if ready:
        for i, (_name, fid) in enumerate(ready, start=1):
            steps.append(_rag_step(str(i), file_id=fid))
    else:
        steps.append(_rag_step("1"))
    dep_ids = [s.step_id for s in steps]
    refs = " ".join(f"{{{{{sid}}}}}" for sid in dep_ids)
    extract_id = str(len(steps) + 1)
    steps.append(
        PlanStep(
            step_id=extract_id,
            agent_id="reasoning",
            input={"message": _quiz_extraction_message(refs, request_text, count, kind, grounded=True)},
            depends_on=dep_ids,
            expected_output_type="quiz_json",
        )
    )
    steps.append(
        PlanStep(
            step_id=str(len(steps) + 1),
            tool_id="quiz.generate",
            input={
                "data": f"{{{{{extract_id}}}}}",
                "title": " ".join((request_text or "").split())[:160] or "Quiz",
            },
            depends_on=[extract_id],
            expected_output_type="quiz",
        )
    )
    return Plan(plan_id=str(uuid.uuid4()), goal=request_text, steps=steps)


def build_quiz_general(request_text: str, *, count: int = 10, kind: str = "mcq") -> Plan:
    """General-knowledge quiz: no documents, answer from parametric knowledge.

    Mirrors build_qa_no_docs: emitting rag.query would provably return
    "(no chunks retrieved)" — ask the model to write the test directly.
    """
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                agent_id="reasoning",
                input={"message": _quiz_extraction_message("", request_text, count, kind, grounded=False)},
                expected_output_type="quiz_json",
            ),
            PlanStep(
                step_id="2",
                tool_id="quiz.generate",
                input={
                    "data": "{{1}}",
                    "title": " ".join((request_text or "").split())[:160] or "Quiz",
                },
                depends_on=["1"],
                expected_output_type="quiz",
            ),
        ],
    )


def _chart_type_for(request_text: str) -> str:
    """Deterministic bar/line pick from the request wording (default bar)."""
    if re.search(
        r"\bline\b|\btrend\b|\btimeseries\b|\btime-series\b", request_text.lower()
    ):
        return "line"
    return "bar"


def build_convert_all(target_format: str, request_text: str) -> Plan:
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                tool_id="doc.convert",
                input={"file_id": "*", "target_format": target_format},
                expected_output_type="document",
            )
        ],
    )


def build_convert_one(file_id: str, target_format: str, request_text: str) -> Plan:
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                tool_id="doc.convert",
                input={"file_id": file_id, "target_format": target_format},
                expected_output_type="document",
            )
        ],
    )


def _resolve_code_targets(
    file_hint: str, notebook_context: str | None
) -> list[tuple[str, str]]:
    """Resolve CODE file_hint to [(name, file_id)] code targets.

    Named hint → substring match (case-insensitive) over ready code files;
    empty hint → all ready code files (capped). Never invents an id.
    """
    ready = _ready_code(notebook_context)
    if not ready:
        return []
    hint = (file_hint or "").strip()
    if hint and hint != "*":
        matched = [(n, f) for n, f in ready if hint.lower() in n.lower()]
        return matched[:_CODE_MAX_FILES]
    return ready[:_CODE_MAX_FILES]


def _read_code_file(notebook_id: str, name: str, file_id: str) -> str | None:
    """Read one code file from disk; None when unreadable/untrusted."""
    if not _UUID_RE.match(file_id or ""):
        return None
    if not _SAFE_NOTEBOOK_RE.match(notebook_id or ""):
        return None
    ext = os.path.splitext(name or "")[1].lower()
    if not ext:
        return None
    from app.core.config import settings

    if ext not in set(settings.code_extensions or []):
        return None
    path = os.path.join(settings.upload_dir, notebook_id, f"{file_id}{ext}")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(_CODE_MAX_BYTES_PER_FILE + 1)
    except OSError:
        return None


def _code_files_block(notebook_id: str | None, targets: list[tuple[str, str]]) -> str:
    """Inline target sources as fenced sections (shared by build_code and
    build_code_execute). Unreadable files get an honest note, never silence:
    a gap the agent can misread as "no file attached" (trace 241f3fa1).
    """
    sections: list[str] = []
    total = 0
    for name, fid in targets:
        content = _read_code_file(notebook_id or "", name, fid) if notebook_id else None
        if content is None:
            sections.append(
                f"File {name}: (could not be read — describe it or re-upload it)"
            )
            continue
        truncated = len(content) > _CODE_MAX_BYTES_PER_FILE
        if truncated:
            content = content[:_CODE_MAX_BYTES_PER_FILE]
        if total + len(content) > _CODE_MAX_BYTES_TOTAL:
            room = _CODE_MAX_BYTES_TOTAL - total
            content = content[: max(room, 0)]
            truncated = True
        total += len(content)
        marker = (
            "\n…[truncated — file continues beyond what is shown]" if truncated else ""
        )
        lang = _EXT_TO_LANG.get(os.path.splitext(name)[1].lower(), "text")
        sections.append(f"File {name}:\n```{lang}\n{content}\n```{marker}")
        if total >= _CODE_MAX_BYTES_TOTAL:
            break
    return "\n\n".join(sections)


def _coding_budget_for(request_text: str) -> int:
    """Output budget for a CODE step: generous only for generation."""
    from app.core.config import settings as _settings

    if _REVIEW_RE.search(request_text or ""):
        return _settings.default_max_tokens
    return _settings.coding_max_tokens


def _is_execute_request(request_text: str) -> bool:
    """True when a CODE ask wants execution, not just generated text."""
    text = request_text or ""
    if _REVIEW_RE.search(text):
        return False
    return bool(_EXECUTE_RE.search(text))


def _sandbox_budget_ms() -> int:
    """Sandbox step input budget: tool default, capped like the tool."""
    return min(int(settings.sandbox_timeout_ms), 480000)


def build_code_execute(
    request_text: str,
    file_hint: str = "",
    notebook_context: str | None = None,
    notebook_id: str | None = None,
) -> Plan:
    """Two-step execute DAG: code.sandbox runs, coding reports (ADR-050).

    Observation-only full workflow: sandbox stdout + changed files surface
    via the (hidden) step output and run artifacts for review — originals
    are never overwritten. The coding step carries {{1}} so the validator's
    reasoning-grounding rule holds by construction; its message forbids
    inventing execution results. Target sources are inlined into the report
    prompt (trace 241f3fa1: the sandbox was the only content carrier, so its
    timeout left the reporter blind); upstream failure text arrives via
    {{error:1}}. notebook_id feeds the source inline; the engine also
    injects the run's notebook id into the tool step at runtime.
    """
    targets = _resolve_code_targets(file_hint, notebook_context)
    if not targets and (file_hint or "").strip():
        # User named a file that resolves to nothing: honest
        # clarification, never an invented file or an empty sandbox run.
        detail = (
            f"No uploaded code file matches '{file_hint.strip()}'. Ask the user "
            "to check the file name or upload the file, or clarify how "
            "to proceed without it"
        )
        return Plan(
            plan_id=str(uuid.uuid4()),
            goal=request_text,
            steps=[
                PlanStep(
                    step_id="1",
                    agent_id="coding",
                    input={"message": f"{detail}. Request: {request_text}"},
                    expected_output_type="clarification",
                )
            ],
        )
    sandbox_input: dict = {
        "task": request_text,
        "timeout_ms": _sandbox_budget_ms(),
    }
    if targets:
        sandbox_input["file_names"] = [name for name, _ in targets]
    # Greenfield (no hint, no files): sandbox runs task-only; the tool
    # notes the empty staging honestly in its observation.
    if targets:
        source_section = (
            "Attached source files (these ARE present — "
            "never claim otherwise):\n" + _code_files_block(notebook_id, targets)
        )
    else:
        source_section = "Attached source files: none."
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                tool_id="code.sandbox",
                input=sandbox_input,
                expected_output_type="text",
            ),
            PlanStep(
                step_id="2",
                agent_id="coding",
                input={
                    "message": (
                        f"Request: {request_text}\n\n{source_section}\n\n"
                        "Sandbox execution result:\n{{1}}{{error:1}}\n\n"
                        "Report only what the box returned: start your answer "
                        "directly with the execution output (stdout and exit "
                        "status), then the files changed (available under "
                        "Artifacts for review) and concrete next steps. "
                        "Do not narrate your own process — no 'I will explore', "
                        "no shell listings, no future plans: the code already "
                        "ran in the sandbox. "
                        "Never invent execution results, and never claim the "
                        "code ran if the result shows a failure. "
                        "If the sandbox result shows a failure: say what "
                        "failed and quote the error first; use the attached "
                        "source only to ground concrete next steps, and label "
                        "anything you draft yourself UNTESTED — never present "
                        "it as executed output."
                    ),
                    "max_tokens": _coding_budget_for(request_text),
                },
                depends_on=["1"],
                expected_output_type="answer",
            ),
        ],
    )


def _resolve_data_targets(
    file_hint: str, notebook_context: str | None
) -> list[tuple[str, str]]:
    """Resolve a file hint to [(name, file_id)] DATA (CSV) targets.

    Named hint → substring match (case-insensitive) over ready data files;
    empty hint → all ready data files (capped). Never invents an id.
    """
    ready = _ready_data(notebook_context)
    if not ready:
        return []
    hint = (file_hint or "").strip()
    if hint and hint != "*":
        matched = [(n, f) for n, f in ready if hint.lower() in n.lower()]
        return matched[:_DATA_MAX_FILES]
    return ready[:_DATA_MAX_FILES]


def _is_csv_hint(file_hint: str) -> bool:
    """True when the router file_hint names a CSV file."""
    return bool(_DATA_HINT_RE.search(file_hint or ""))


def _read_csv_head(notebook_id: str, name: str, file_id: str) -> str | None:
    """Read a CSV head (header + sample rows); None when unreadable/untrusted."""
    if not _UUID_RE.match(file_id or ""):
        return None
    if not _SAFE_NOTEBOOK_RE.match(notebook_id or ""):
        return None
    ext = os.path.splitext(name or "")[1].lower()
    if ext != ".csv":
        return None
    from app.core.config import settings as _settings

    if ext not in set(_settings.data_extensions or []):
        return None
    path = os.path.join(_settings.upload_dir, notebook_id, f"{file_id}{ext}")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = []
            total = 0
            # Header + sample: enough for the model to name columns correctly
            # without inlining the whole file (context safety).
            for _ in range(_DATA_MAX_HEAD_ROWS + 1):
                line = f.readline()
                if not line:
                    break
                lines.append(line.rstrip("\n"))
                total += len(line)
                if total > _DATA_MAX_HEAD_BYTES:
                    break
            return "\n".join(lines) if lines else None
    except OSError:
        return None


def build_csv_analyze(
    request_text: str,
    file_hint: str = "",
    notebook_context: str | None = None,
    notebook_id: str | None = None,
) -> Plan:
    """Two-step CSV DAG: code.sandbox (pandas) runs, coding reports.

    Mirrors build_code_execute: the CSV rides `data_files` (staged read-only,
    never diffed as changed), the coding step carries {{1}} so the
    validator's reasoning-grounding rule holds by construction, and only a
    header + sample rides the report prompt — the full file is read by
    pandas inside the box, never inlined. notebook_id is unused here: the
    engine injects the run's notebook id into the tool step at runtime.
    """
    targets = _resolve_data_targets(file_hint, notebook_context)
    if not targets:
        if (file_hint or "").strip():
            detail = (
                f"No uploaded data file matches '{file_hint.strip()}'. Ask the user "
                "to check the file name or upload the CSV, or clarify how "
                "to proceed without it"
            )
        else:
            detail = (
                "There is no ready CSV data file in this notebook. Ask the user "
                "to upload a CSV file or clarify how to proceed without one"
            )
        return Plan(
            plan_id=str(uuid.uuid4()),
            goal=request_text,
            steps=[
                PlanStep(
                    step_id="1",
                    agent_id="coding",
                    input={"message": f"{detail}. Request: {request_text}"},
                    expected_output_type="clarification",
                )
            ],
        )
    sandbox_input: dict = {
        "task": request_text,
        "timeout_ms": _sandbox_budget_ms(),
        "data_files": [name for name, _ in targets],
    }
    code_targets = _resolve_code_targets(file_hint, notebook_context)
    if code_targets:
        sandbox_input["file_names"] = [name for name, _ in code_targets]
    head_sections: list[str] = []
    for name, fid in targets:
        head = _read_csv_head(notebook_id or "", name, fid) if notebook_id else None
        if head is None:
            head_sections.append(
                f"CSV {name}: (could not be read — describe it or re-upload it)"
            )
        else:
            head_sections.append(f"CSV {name} head (header + sample rows):\n```csv\n{head}\n```")
    head_block = "\n\n".join(head_sections)
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                tool_id="code.sandbox",
                input=sandbox_input,
                expected_output_type="text",
            ),
            PlanStep(
                step_id="2",
                agent_id="coding",
                input={
                    "message": (
                        f"Request: {request_text}\n\n{head_block}\n\n"
                        "Sandbox execution result:\n{{1}}{{error:1}}\n\n"
                        "Report only what the box returned: start your answer "
                        "directly with the analysis output (summary statistics, "
                        "findings, exit status), then the files changed "
                        "(available under Artifacts for review) and concrete "
                        "next steps. The CSV head above is for column names "
                        "only — every number must come from the sandbox "
                        "result, never from the head sample. "
                        "Do not narrate your own process — no 'I will explore', "
                        "no shell listings, no future plans: the analysis already "
                        "ran in the sandbox. "
                        "Never invent results, and never claim the analysis ran "
                        "if the result shows a failure. "
                        "If the sandbox result shows a failure: say what "
                        "failed and quote the error first, then ground next "
                        "steps in the CSV head above."
                    ),
                    "max_tokens": _coding_budget_for(request_text),
                },
                depends_on=["1"],
                expected_output_type="answer",
            ),
        ],
    )


def build_code(
    request_text: str,
    file_hint: str = "",
    notebook_context: str | None = None,
    notebook_id: str | None = None,
) -> Plan:
    """Single coding-agent step, file-backed or greenfield.

    Generate-and-present only: no tools, no execution, no placeholders.
    Code files bypass vector ingest, so there are no chunks to ground —
    the file content rides in the message itself. When no code files
    resolve and the request names none, this is greenfield generation
    (write new code from scratch) — not a missing-file situation.
    """
    targets = _resolve_code_targets(file_hint, notebook_context)
    if not (file_hint or "").strip() and len(_ready_code(notebook_context)) > 1:
        # Vague ask ("review the python code") with several candidates:
        # inlining all of them blew the 300s wall-clock (validator.py trace).
        # Ask which file instead of reviewing everything.
        names = ", ".join(n for n, _ in _ready_code(notebook_context)[:5])
        detail = (
            f"There are several code files ({names}). Ask the user to name "
            "the file to review, or clarify whether to review all of them"
        )
        return Plan(
            plan_id=str(uuid.uuid4()),
            goal=request_text,
            steps=[
                PlanStep(
                    step_id="1",
                    agent_id="coding",
                    input={
                        "message": f"{detail}. Request: {request_text}",
                        "max_tokens": settings.default_max_tokens,
                    },
                    expected_output_type="clarification",
                )
            ],
        )
    if not targets:
        if (file_hint or "").strip():
            # User named a file that resolves to nothing: honest
            # clarification, never an invented file.
            detail = (
                f"No uploaded code file matches '{file_hint.strip()}'. Ask the user "
                "to check the file name or upload the file, or clarify how "
                "to proceed without it"
            )
            return Plan(
                plan_id=str(uuid.uuid4()),
                goal=request_text,
                steps=[
                    PlanStep(
                        step_id="1",
                        agent_id="coding",
                        input={"message": f"{detail}. Request: {request_text}"},
                        expected_output_type="clarification",
                    )
                ],
            )
        # Greenfield: no files attached, none named — generate from the
        # request alone (e.g. "generate a landing page" → HTML/CSS/JS).
        return Plan(
            plan_id=str(uuid.uuid4()),
            goal=request_text,
            steps=[
                PlanStep(
                    step_id="1",
                    agent_id="coding",
                    input={
                        "message": f"Request: {request_text}",
                        "max_tokens": _coding_budget_for(request_text),
                    },
                    expected_output_type="answer",
                )
            ],
        )
    files_block = _code_files_block(notebook_id, targets)
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                agent_id="coding",
                input={
                    # Generation gets headroom so output is cut by content,
                    # never by the token cap; reviews reuse the shared default.
                    "message": (
                        f"Request: {request_text}\n\n{files_block}"
                    ),
                    "max_tokens": _coding_budget_for(request_text),
                },
                expected_output_type="answer",
            )
        ],
    )


def _resolve_convert_file_id(
    file_hint: str, notebook_context: str | None
) -> str | None:
    """Resolve a router file_hint to a literal snapshot file_id.

    Returns None when unresolvable (no hint, no snapshot, no/ambiguous
    match, or match not ready) — caller falls through to L3 ReAct, which
    asks the counter-question. Never invents an id.
    """
    hint = (file_hint or "").strip()
    if not hint or hint == "*":
        return None
    candidates = [
        (name, fid)
        for name, status, fid in _snapshot_files(notebook_context)
        if status == "ready" and hint.lower() in name.lower()
    ]
    if len(candidates) != 1:
        return None
    return candidates[0][1]


def build(
    request_text: str,
    route: RouterResult,
    notebook_context: str | None = None,
    notebook_id: str | None = None,
) -> Plan | None:
    """Dispatch router result to a deterministic builder.

    Returns None for intents without a fixed shape — caller falls through
    to L3 ReAct.
    """
    if route.intent is Intent.CHAT:
        return build_chat(request_text)
    if route.intent is Intent.QA_SINGLE:
        state = get_corpus_state(notebook_context)
        if state == "empty":
            # Data-only notebook (CSV, no embeddings): analyze with pandas
            # instead of answering from parametric knowledge.
            if _ready_data(notebook_context):
                return build_csv_analyze(
                    request_text, route.file_hint, notebook_context, notebook_id
                )
            return build_qa_no_docs(request_text)
        if state == "processing":
            return build_no_docs_clarification(
                request_text, processing=True, intent=route.intent
            )
        return build_qa_single(request_text, request_text)
    if route.intent is Intent.COMPARE_MULTI:
        state = get_corpus_state(notebook_context)
        if state in ("empty", "processing"):
            if state == "empty" and _ready_data(notebook_context):
                return build_csv_analyze(
                    request_text, route.file_hint, notebook_context, notebook_id
                )
            return build_no_docs_clarification(
                request_text, processing=(state == "processing"),
                intent=route.intent,
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None  # too many files: fall through to L3 ReAct
        return build_compare_multi([], request_text, notebook_context)
    if route.intent is Intent.SUMMARIZE:
        state = get_corpus_state(notebook_context)
        if state in ("empty", "processing"):
            if state == "empty" and _ready_data(notebook_context):
                return build_csv_analyze(
                    request_text, route.file_hint, notebook_context, notebook_id
                )
            return build_no_docs_clarification(
                request_text, processing=(state == "processing"),
                intent=route.intent,
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None
        return build_summarize(request_text, notebook_context)
    if route.intent is Intent.SUMMARIZE_PLOT:
        state = get_corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing"),
                intent=route.intent,
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None  # too many files: fall through to L3 ReAct
        return build_summarize_plot(request_text, notebook_context)
    if route.intent is Intent.QUIZ:
        state = get_corpus_state(notebook_context)
        count, kind = parse_quiz_params(request_text)
        # Router slots win when explicitly set (ReAct/tests may carry them).
        if getattr(route, "quiz_count", 0):
            try:
                count = max(2, min(50, int(route.quiz_count)))
            except (TypeError, ValueError):
                pass
        if getattr(route, "quiz_kind", "") in ("mcq", "msq", "truefalse"):
            kind = route.quiz_kind
        if state == "processing":
            return build_no_docs_clarification(
                request_text, processing=True,
                intent=route.intent,
            )
        if state == "empty":
            # General-knowledge test (no documents): write from parametric
            # knowledge instead of refusing — mirrors qa_single no-docs path.
            return build_quiz_general(request_text, count=count, kind=kind)
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None
        return build_quiz(request_text, request_text, notebook_context, count=count, kind=kind)
    if route.intent is Intent.CONVERT_ALL:
        if not route.target_format:
            return None
        return build_convert_all(route.target_format, request_text)
    if route.intent is Intent.CONVERT_ONE:
        if not route.target_format:
            return None
        file_id = _resolve_convert_file_id(route.file_hint, notebook_context)
        if file_id is None:
            return None
        return build_convert_one(file_id, route.target_format, request_text)
    if route.intent is Intent.CODE:
        # CSV data asks always need compute (pandas in the sandbox) — even
        # without run/test verbs ("summarize sales.csv" is an analysis, not
        # a code review). Named-CSV hints and data-only notebooks route here;
        # everything else keeps the execute/generate split below.
        if _is_csv_hint(route.file_hint) or (
            not (route.file_hint or "").strip()
            and _ready_data(notebook_context)
            and not _resolve_code_targets(route.file_hint, notebook_context)
            and _DATA_SIGNAL_RE.search(request_text or "")
        ):
            return build_csv_analyze(
                request_text, route.file_hint, notebook_context, notebook_id
            )
        if _is_execute_request(request_text):
            return build_code_execute(request_text, route.file_hint, notebook_context, notebook_id)
        return build_code(request_text, route.file_hint, notebook_context, notebook_id)
    return None
