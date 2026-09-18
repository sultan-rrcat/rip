"""L2a deterministic builders — code-built DAGs, no DAG-LLM.

For intents in DETERMINISTIC_INTENTS the plan shape is fixed; only slot
values (queries, request text) vary. Wiring (depends_on + {{id}}
placeholders) is set by construction, so the ecd93eb4 failure class
(prose mention of steps without placeholders) cannot occur.

Deliberately NOT built: summarize_plot — a plot needs content-derived
`labels` no deterministic shape can know (inventing them would be the
hallucinated-chart class ADR-027 exists to prevent), so it stays on the
L3 mega-prompt (Example E). Convert builders resolve literal file ids
from the notebook snapshot; anything unresolvable returns None → L3.
"""

from __future__ import annotations

import re
import uuid

from app.orchestration.intents import Intent
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.router import RouterResult

#: Snapshot lines look like "report.pdf [ready] id=abc123" (see
#: runs/manager._load_file_snapshot). Only ready files convert.
_SNAPSHOT_FILE = re.compile(r"(.+?)\s*\[(ready|processing|uploading|error)\]\s*id=(\S+)")


def _snapshot_files(notebook_context: str | None) -> list[tuple[str, str, str]]:
    """Parse (name, status, file_id) triples out of the snapshot string."""
    if not notebook_context:
        return []
    cleaned = []
    for name, status, fid in _SNAPSHOT_FILE.findall(notebook_context):
        fid_clean = fid.strip().rstrip(";,")
        if fid_clean:
            cleaned.append((name.strip(), status.strip().lower(), fid_clean))
    return cleaned


def _ready_files(notebook_context: str | None) -> list[tuple[str, str]]:
    """Return (name, file_id) for ALL ready files in snapshot order."""
    return [
        (name, fid)
        for name, status, fid in _snapshot_files(notebook_context)
        if status == "ready" and fid
    ]


_PER_FILE_TOP_K = 4


def _corpus_state(notebook_context: str | None) -> str:
    """Tri-state the notebook file snapshot for empty-corpus guards.

    Returns one of:
    - "unknown": snapshot is None (DB failure) — caller should still try
      rag.query; the database is ground truth, not the snapshot.
    - "ready": at least one ready file — document-grounded retrieval path.
    - "processing": files exist but none is ready yet (uploading /
      processing / error) — nothing retrievable right now.
    - "empty": known to hold zero files — retrieval would provably return
      "(no chunks retrieved)".
    """
    if notebook_context is None:
        return "unknown"
    files = _snapshot_files(notebook_context)
    if not files:
        return "empty"
    if any(status == "ready" and fid for _, status, fid in files):
        return "ready"
    return "processing"


def build_qa_no_docs(request_text: str) -> Plan:
    """Answer a factual question with no retrievable documents.

    Empty-corpus path: emitting rag.query would provably return
    "(no chunks retrieved)" and force the grounded prompt to answer
    "not in the documents" — useless for general-knowledge questions
    like "What is QLoRA?". Answer parametrically with a disclaimer.
    """
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                agent_id="reasoning",
                input={
                    "message": (
                        "{request_text}"
                    )
                },
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
                input={"message": request_text},
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
                        f"chunks are empty. Request: {request_text}"
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
                    f"chunks do not cover. Request: {request_text}"
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
                    f"chunks are empty. Request: {request_text}"
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
                        f"the documents' when the chunks are empty. "
                        f"Request: {request_text}"
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
                        f"the documents' when the chunks are empty. "
                        f"Request: {request_text}"
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


def _resolve_convert_file_id(
    file_hint: str, notebook_context: str | None
) -> str | None:
    """Resolve a router file_hint to a literal snapshot file_id.

    Returns None when unresolvable (no hint, no snapshot, no/ambiguous
    match, or match not ready) — caller falls through to L3, which asks
    the counter-question. Never invents an id.
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
) -> Plan | None:
    """Dispatch router result to a deterministic builder.

    Returns None for intents without a fixed shape — caller falls through
    to L2b specialist / L3 mega-prompt.
    """
    if route.intent is Intent.CHAT:
        return build_chat(request_text)
    if route.intent is Intent.QA_SINGLE:
        state = _corpus_state(notebook_context)
        if state == "empty":
            return build_qa_no_docs(request_text)
        if state == "processing":
            return build_no_docs_clarification(request_text, processing=True)
        query = route.queries[0] if route.queries else request_text
        return build_qa_single(query, request_text)
    if route.intent is Intent.COMPARE_MULTI:
        state = _corpus_state(notebook_context)
        if state in ("empty", "processing"):
            return build_no_docs_clarification(
                request_text, processing=(state == "processing")
            )
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None  # too many files: fall through to L3 mega-prompt
        return build_compare_multi(route.queries, request_text, notebook_context)
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
        query = route.queries[0] if route.queries else request_text
        ready = _ready_files(notebook_context)
        if ready and len(ready) > 5:
            return None
        return build_quiz(query, request_text, notebook_context)
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
    return None
