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
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_NOTEBOOK_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

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
#: summarize, quiz). Grounding invariants ("ONLY chunks", "not in the
#: documents", no placeholder leaks) come first; this only styles HOW the
#: honest answer reads in chat. Kept short — the reasoning system prompt
#: carries the full voice.
_PRESENTATION_SUFFIX = (
    " Present like a world-class assistant: lead with the direct answer, "
    "then supporting detail in clear Markdown (short headings, bullets, "
    "numbered steps, or a table when it helps)."
)


def _corpus_state(notebook_context: str | None) -> str:
    """Backwards-compat wrapper over `corpus.get_corpus_state`.

    Kept so existing importers (`react.py` legacy, tests) keep working;
    new code should import from `app.orchestration.corpus` directly.
    """
    return get_corpus_state(notebook_context)


def build_qa_no_docs(request_text: str) -> Plan:
    """Answer a factual question with no retrievable documents.

    Empty-corpus path: emitting rag.query would provably return
    "(no chunks retrieved)" and force the grounded prompt to answer
    "not in the documents" — useless for general-knowledge questions
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
                        f"Answer the user's request using ONLY these retrieved "
                        f"chunks {{{{1}}}}. Say 'not in the documents' when the "
                        f"chunks are empty or read '(no chunks retrieved)'. "
                        f"Never mention chunk ids or placeholders. "
                        f"Request: {request_text}{_PRESENTATION_SUFFIX}"
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
                    f"request. Say 'not in the documents' for anything the "
                    f"chunks do not cover, including when they read "
                    f"'(no chunks retrieved)'. Never mention chunk ids or "
                    f"placeholders. Request: {request_text}{_PRESENTATION_SUFFIX}"
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
                    f"requested summary. Say 'not in the documents' when the "
                    f"chunks are empty or read '(no chunks retrieved)'. "
                    f"Never mention chunk ids or placeholders. "
                    f"Request: {request_text}{_PRESENTATION_SUFFIX}"
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

    Unlike plot_standalone, the chart literals live in the documents, not
    the message — so they must be extracted. ONE reasoning step (eot
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
                    f"requested summary. Say 'not in the documents' when the "
                    f"chunks are empty or read '(no chunks retrieved)'. "
                    f"Never mention chunk ids or placeholders. "
                    f"Request: {request_text}{_PRESENTATION_SUFFIX}"
                )
            },
            depends_on=dep_ids,
            expected_output_type="answer",
        )
    )
    return Plan(plan_id=str(uuid.uuid4()), goal=request_text, steps=steps)


def build_quiz(
    query: str,
    request_text: str,
    notebook_context: str | None = None,
) -> Plan:
    """Per-file overview shards, then ONE writer step.

    Single writer (never fan-out) per planner Rule 9: splitting by
    difficulty costs more and contends the single Ollama server.
    Overview mode gives breadth for question coverage.
    """
    ready = _ready_files(notebook_context)[:5]
    steps: list[PlanStep] = []
    if ready and len(ready) > 1:
        for i, (_name, fid) in enumerate(ready, start=1):
            steps.append(
                PlanStep(
                    step_id=str(i),
                    tool_id="rag.query",
                    input={
                        "query": query or request_text,
                        "top_k": _PER_FILE_TOP_K,
                        "file_id": fid,
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
                        f"Using ONLY these retrieved chunks ({refs}), "
                        f"write the requested questions/quiz. Say 'not in "
                        f"the documents' when the chunks are empty or read "
                        f"'(no chunks retrieved)'. Never mention chunk ids "
                        f"or placeholders. "
                        f"Request: {request_text}{_PRESENTATION_SUFFIX}"
                    )
                },
                depends_on=dep_ids,
                expected_output_type="answer",
            )
        )
        return Plan(plan_id=str(uuid.uuid4()), goal=request_text, steps=steps)
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
                    "mode": "overview",
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
                        f"Using ONLY these retrieved chunks {{{{1}}}}, "
                        f"write the requested questions/quiz. Say 'not in "
                        f"the documents' when the chunks are empty or read "
                        f"'(no chunks retrieved)'. Never mention chunk ids "
                        f"or placeholders. "
                        f"Request: {request_text}{_PRESENTATION_SUFFIX}"
                    )
                },
                depends_on=["1"],
                expected_output_type="answer",
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


#: "Label: 12.5" / "Label = 12.5" pairs — explicit labels stay with numbers.
_PAIR_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 _.\-]{0,40}?)\s*[:=]\s*(-?\d[\d,]*\.?\d*\s*%?)"
)
#: Bare numbers fallback when the message carries no explicit labels.
_NUMBER_RE = re.compile(r"(?<![\w:.=-])-?\d[\d,]*\.?\d*\s*%?(?![\w%])")


def _clean_pair_label(raw_label: str) -> str:
    """Trim sentence spillover off a `label: number` match.

    The pair regex can swallow preceding prose ("plot Alpha: 10" matches
    "plot Alpha"). Leading lowercase words are sentence verbs, not labels —
    drop them only when a capitalized word follows ("plot Alpha" → "Alpha",
    "compare A" → "A") while keeping genuine multi-word labels intact
    ("North America", "total revenue").
    """
    words = (raw_label or "").split()
    while (
        len(words) > 1
        and words[0][:1].islower()
        and any(w[:1].isupper() for w in words[1:])
    ):
        words.pop(0)
    return " ".join(words)


def _parse_standalone_numbers(
    request_text: str,
) -> tuple[list[str], list[float]] | None:
    """Parse chart literals out of the message text (no LLM, no invention).

    Explicit `label: number` pairs win (labels grounded in the user's own
    words); otherwise bare numbers get positional "Point N" labels. Returns
    None when fewer than 2 or more than 50 numbers are found — caller falls
    through to L3 ReAct.
    """
    pairs: list[tuple[str, float]] = []
    for raw_label, raw_number in _PAIR_RE.findall(request_text or ""):
        try:
            value = float(raw_number.replace(",", "").rstrip("%").strip())
        except ValueError:
            continue
        label = _clean_pair_label(raw_label)
        if label:
            pairs.append((label, value))
    if pairs:
        labels = [label for label, _ in pairs[:50]]
        values = [value for _, value in pairs[:50]]
        if 2 <= len(values) <= 50:
            return labels, values
        return None
    values: list[float] = []
    for raw in _NUMBER_RE.findall(request_text or ""):
        try:
            values.append(float(raw.replace(",", "").rstrip("%").strip()))
        except ValueError:
            continue
        if len(values) > 50:
            return None
    if 2 <= len(values) <= 50:
        return [f"Point {i}" for i in range(1, len(values) + 1)], values
    return None


def build_plot_standalone(request_text: str) -> Plan | None:
    """Single literal plot.chart step from message-parsed numbers.

    Both labels and values come from the message, so the pair count always
    matches by construction and nothing is invented — this is the shape
    ADR-027 permits for plots. Standalone plots with user-given literals
    stay validator-legal (no depends_on, no placeholders). Unparseable
    messages return None → L3 ReAct, which recalls figures via reasoning.
    """
    parsed = _parse_standalone_numbers(request_text)
    if parsed is None:
        return None
    labels, values = parsed
    title = " ".join((request_text or "").split())[:160] or "Chart"
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                tool_id="plot.chart",
                input={
                    "chart_type": _chart_type_for(request_text),
                    "labels": labels,
                    "values": values,
                    "title": title,
                },
                expected_output_type="chart",
            )
        ],
    )


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


def _coding_budget_for(request_text: str) -> int:
    """Output budget for a CODE step: generous only for generation."""
    from app.core.config import settings as _settings

    if _REVIEW_RE.search(request_text or ""):
        return _settings.default_max_tokens
    return _settings.coding_max_tokens


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
    sections: list[str] = []
    total = 0
    for name, fid in targets:
        content = _read_code_file(notebook_id or "", name, fid) if notebook_id else None
        if content is None:
            sections.append(f"File {name}: (could not be read — describe it or re-upload it)")
            continue
        truncated = len(content) > _CODE_MAX_BYTES_PER_FILE
        if truncated:
            content = content[:_CODE_MAX_BYTES_PER_FILE]
        if total + len(content) > _CODE_MAX_BYTES_TOTAL:
            room = _CODE_MAX_BYTES_TOTAL - total
            content = content[: max(room, 0)]
            truncated = True
        total += len(content)
        marker = "\n…[truncated — file continues beyond what is shown]" if truncated else ""
        lang = _EXT_TO_LANG.get(os.path.splitext(name)[1].lower(), "text")
        sections.append(f"File {name}:\n```{lang}\n{content}\n```{marker}")
        if total >= _CODE_MAX_BYTES_TOTAL:
            break
    files_block = "\n\n".join(sections)
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
        state = _corpus_state(notebook_context)
        if state == "empty":
            return build_qa_no_docs(request_text)
        if state == "processing":
            return build_no_docs_clarification(
                request_text, processing=True, intent=route.intent
            )
        return build_qa_single(request_text, request_text)
    if route.intent is Intent.COMPARE_MULTI:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing"),
                intent=route.intent,
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None  # too many files: fall through to L3 ReAct
        return build_compare_multi([], request_text, notebook_context)
    if route.intent is Intent.SUMMARIZE:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing"),
                intent=route.intent,
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None
        return build_summarize(request_text, notebook_context)
    if route.intent is Intent.SUMMARIZE_PLOT:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing"),
                intent=route.intent,
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None  # too many files: fall through to L3 ReAct
        return build_summarize_plot(request_text, notebook_context)
    if route.intent is Intent.PLOT_STANDALONE:
        return build_plot_standalone(request_text)
    if route.intent is Intent.QUIZ:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing"),
                intent=route.intent,
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None
        return build_quiz(request_text, request_text, notebook_context)
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
        return build_code(request_text, route.file_hint, notebook_context, notebook_id)
    return None
