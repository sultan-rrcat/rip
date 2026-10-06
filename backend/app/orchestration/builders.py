"""L2 deterministic builders — code-built DAGs, no DAG-LLM.

For intents in DETERMINISTIC_INTENTS the plan shape is fixed; only slot
values (queries, request text) vary. Wiring (depends_on + {{id}}
placeholders) is set by construction, so the ecd93eb4 failure class
(prose mention of steps without placeholders) cannot occur.

Deliberately NOT built: summarize_plot — a plot needs content-derived
`labels` no deterministic shape can know (inventing them would be the
hallucinated-chart class ADR-027 exists to prevent), so it goes to
L3 ReAct. Convert builders resolve literal file ids from the notebook
snapshot; anything unresolvable returns None → L3 ReAct.
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
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_NOTEBOOK_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

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


def build_no_docs_clarification(request_text: str, *, processing: bool = False) -> Plan:
    """Ask the user to upload/wait — nothing exists to summarize/compare.

    expected_output_type="clarification" so the single terminal step is
    returned verbatim as the answer (no retrieval to ground anything else).
    """
    if processing:
        detail = (
            "The notebook's files are not ready yet (still uploading, "
            "processing, or errored). Ask the user to wait until "
            "processing finishes and then retry"
        )
    else:
        detail = (
            "There are no ready documents in this notebook. Ask the user "
            "to upload documents or clarify how to proceed without them"
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
                    input={"query": q, "top_k": _PER_FILE_TOP_K, "mode": "specific"},
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


def build_code(
    request_text: str,
    file_hint: str = "",
    notebook_context: str | None = None,
    notebook_id: str | None = None,
) -> Plan:
    """Single coding-agent step with code file(s) inlined as text.

    Generate-and-present only: no tools, no execution, no placeholders.
    Code files bypass vector ingest, so there are no chunks to ground —
    the file content rides in the message itself.
    """
    targets = _resolve_code_targets(file_hint, notebook_context)
    if not targets:
        detail = (
            "There are no ready code (.py) files in this notebook. Ask the user "
            "to upload a Python file or clarify how to proceed without one"
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
        sections.append(f"File {name}:\n```python\n{content}\n```{marker}")
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
                    # Test scripts + file echoes are long: generous budget so
                    # output is cut by content, never by the token cap.
                    "message": (
                        f"Request: {request_text}\n\n{files_block}"
                    ),
                    "max_tokens": settings.coding_max_tokens,
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
            return build_no_docs_clarification(request_text, processing=True)
        return build_qa_single(request_text, request_text)
    if route.intent is Intent.COMPARE_MULTI:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing")
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None  # too many files: fall through to L3 ReAct
        return build_compare_multi([], request_text, notebook_context)
    if route.intent is Intent.SUMMARIZE:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing")
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None
        return build_summarize(request_text, notebook_context)
    if route.intent is Intent.QUIZ:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing")
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
