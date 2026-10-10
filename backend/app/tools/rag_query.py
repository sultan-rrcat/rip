"""rag.query tool — document retrieval over the lifespan VectorRAG singleton.

Locked contract (MERGE_PLAN Q6/Q30): ``rag.query(notebook_id, query, top_k=4)``
reuses the ``VectorRAG`` instantiated once at application lifespan — never
re-instantiated per call (reloading BGE-M3 + reranker weights per query would
spike). ``notebook_id`` is injected by the orchestrator from ``Run.notebook_id``,
never LLM-generated. The return shape feeds ``extract_sources()`` so the run
worker can emit the SSE ``sources`` event (Q32).

RIP port: full rewrite — Athena's anchor-endpoint version (RAG_BASE_URL,
PluginContext/PluginHealth, ToolPlugin) is gone. This module stays light: no
top-level import of ``app.rag.vector_rag`` (that chain pulls torch; the rag
object is duck-typed and injected), only ``app.services.chat`` (pure stdlib).

Effect class: read-only.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from app.core.config import settings
from app.observability.langfuse import (
    get_trace_context,
    manual_span,
    truncate,
    update_current_span,
)
from app.services.chat import extract_sources, format_context_for_llm
from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.rag_query")

_DEFAULT_TOP_K = 4

#: Fire the relevance filter only when the formatted output exceeds this.
#: 12000 matches the downstream ReAct observation budget, so filtered
#: output always fits where it is going. Callers that need every chunk
#: verbatim (L2 builders, whose reasoning steps ground on full text via
#: {{id}} placeholders) pass `verbatim: true` to skip filtering entirely.
_FILTER_THRESHOLD_CHARS = 12000

#: Output cap for the sub-query planner: 1-3 short queries. Thinking burns
#: the same num_predict budget as the answer, so the shared 2048 default
#: would let a think-burn run 8x longer than the output needs.
_SUBQUERY_MAX_TOKENS = 256

#: Narrow schema for the relevance filter: a flat list of verbatim quotes.
#: Structured output (not free text) so the tool can map each quote back to
#: its source result by substring match — anything paraphrased matches
#: nothing and is dropped by construction, never entering the evidence.
_FILTER_SCHEMA: dict = {
    "type": "object",
    "properties": {"quotes": {"type": "array", "items": {"type": "string"}}},
    "required": ["quotes"],
}

_FILTER_SYSTEM_PROMPT = (
    "You are a retrieval relevance filter. Given the user query and the "
    "retrieved chunks below, return the chunks relevant to the query. "
    "Copy each relevant chunk VERBATIM, including its header line, without "
    "paraphrasing, summarizing, or inventing anything. Return an empty list "
    "when none of the chunks is relevant."
)

_VALID_MODES = frozenset({"specific", "overview"})

# Module-global singleton slot. Bound at lifespan (Phase 4.2 main.py calls
# bind_rag_singleton(app.state.rag)) or directly in tests via RagQueryTool(rag=...).
_rag_singleton: Any | None = None


def _norm(text: object) -> str:
    """Collapse whitespace so verbatim quotes match despite formatting."""
    return " ".join(str(text or "").split())


def _map_quotes_to_results(
    quotes: object, results: list[dict]
) -> list[dict]:
    """Keep results covered by the model's verbatim quotes, in doc order.

    A result is kept when its normalized content contains (or is contained
    in) a normalized non-empty quote — partial quotes still match their
    source chunk, while paraphrases match nothing and are dropped. Returns
    [] when nothing maps, which the caller treats as "keep unfiltered".
    """
    normed = []
    if isinstance(quotes, list):
        normed = [_norm(q) for q in quotes if isinstance(q, str) and _norm(q)]
    if not normed:
        return []
    kept: list[dict] = []
    for r in results or []:
        content = _norm(
            (r.get("content") if isinstance(r, dict) else None)
            or (r.get("chunk_text") if isinstance(r, dict) else None)
        )
        if not content:
            continue
        if any(q in content or content in q for q in normed):
            kept.append(r)
    return kept


def _shard_summary(stats: dict, result_count: int) -> dict:
    """Glanceable per-shard funnel for the `rag.shard:N` span output.

    Missing keys (legacy doubles without stats) read "?" so a thin
    corpus is distinguishable from a blind spot in instrumentation.
    """
    get = stats.get if isinstance(stats, dict) else (lambda _k, d=None: d)
    return {
        "results": result_count,
        "vector": f"{get('vector_raw', '?')}->{get('vector_kept', '?')}",
        "fts": get("fts_raw", "?"),
        "merged": get("merged", "?"),
        "reranked": get("reranked", "?"),
        "selected": get("selected", "?"),
        "top_scores": get("top_scores", []),
        "timings_ms": get("timings_ms", {}),
    }


def _retrieval_summary(
    *,
    corpus: dict,
    wholefile_hit: bool,
    shard_counts: list[int],
    merged_total: int,
    filtered: bool,
    filter_reason: str,
    returned: int,
) -> str:
    """One-line retrieval story for the `tool:rag.query` span metadata.

    Capped for the 200-char metadata budget: counts survive, prose doesn't.
    """
    shards = ",".join(str(n) for n in shard_counts) or "-"
    if wholefile_hit:
        path = "whole=hit"
    else:
        path = f"whole=skip:{corpus.get('wholefile_verdict', '?')}"
    return (
        f"corpus={corpus.get('total_chunks', '?')}ch/"
        f"{len(corpus.get('files', []))}f {path} "
        f"shards=[{shards}] merged={merged_total} "
        f"filter={'yes' if filtered else 'no'}:{filter_reason} "
        f"returned={returned}"
    )


def bind_rag_singleton(rag: Any) -> None:
    """Bind the lifespan VectorRAG singleton for rag.query calls."""
    global _rag_singleton
    _rag_singleton = rag


def get_rag_singleton() -> Any:
    """Resolve the VectorRAG singleton: explicit binding first, then
    app.state.rag (set by lifespan). Raises RuntimeError when unbound —
    the tool converts this to an honest ok=False response."""
    if _rag_singleton is not None:
        return _rag_singleton
    try:
        from app.main import app as fastapi_app
    except Exception as e:
        raise RuntimeError(
            "VectorRAG singleton is not bound (lifespan has not run "
            "and app.state.rag is unreachable)"
        ) from e
    rag = getattr(getattr(fastapi_app, "state", None), "rag", None)
    if rag is None:
        raise RuntimeError("VectorRAG singleton is not bound (lifespan has not run)")
    return rag


def rag_query(
    notebook_id: str,
    query: str,
    top_k: int = _DEFAULT_TOP_K,
    *,
    rag: Any | None = None,
    file_id: str | None = None,
    file_name: str | None = None,
    mode: str = "specific",
    collect_stats: list | None = None,
) -> list[dict]:
    """Search notebook documents; return chunk-level results with source metadata.

    Each result: {content, source (file name), section (H1>H2>H3 path),
    rerank_score}. ``file_id``/``file_name`` scope retrieval to one file
    (literals from the notebook snapshot, never LLM-invented); ``mode``
    is ``specific`` (topical rank) or ``overview`` (stratified one-per-H1
    sample in doc order). Raises RuntimeError when the singleton is unbound;
    VectorRAG retrieval errors propagate to the caller (the Tool converts
    them to ok=False).

    ``collect_stats``: when a list is given, the call appends this shard's
    ``context["stats"]`` dict (or {} for doubles without one) — the tool
    uses it for per-shard trace spans without changing the return shape.

    Whole-file shortcut: when every chunk in scope fits the context window
    (``settings.rag_whole_file_pct``), the ranked path is skipped entirely and
    all chunks come back in document order with ``rerank_score=None`` (no
    CrossEncoder ran, so there is no score to report). ``ToolResponse.data
    ["whole_file"]`` records which path ran.
    """
    resolved = rag if rag is not None else get_rag_singleton()
    try:
        context = resolved.retrieve_context(
            notebook_id,
            query,
            top_k=top_k,
            file_id=file_id,
            file_name=file_name,
            mode=mode,
        )
    except TypeError:
        # Back-compat with test doubles exposing the legacy
        # retrieve_context(notebook_id, query, top_k) signature.
        context = resolved.retrieve_context(notebook_id, query, top_k=top_k)
    if collect_stats is not None:
        stats = context.get("stats") if isinstance(context, dict) else None
        collect_stats.append(stats if isinstance(stats, dict) else {})
    results = context.get("results", [])
    if not isinstance(results, list):
        raise TypeError("VectorRAG returned a malformed context (no results list)")
    return results


class RagQueryTool(Tool):
    tool_id = "rag.query"
    name = "RAG Query"
    description = (
        "Search the notebook's documents (vector + full-text, BGE reranked) "
        "and return grounded chunks with source metadata. "
        "Set file_id to scope to one file; mode='overview' returns a "
        "stratified one-per-section sample, mode='specific' (default) "
        "returns topical ranking. When the result is large, an LLM relevance "
        "filter keeps only query-relevant chunks quoted verbatim; pass "
        "verbatim=true to skip filtering and always receive every chunk."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string"},
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
            "file_id": {"type": "string"},
            "file_name": {"type": "string"},
            "mode": {"type": "string"},
            "verbatim": {"type": "boolean"},
        },
        "required": ["notebook_id", "query"],
    }
    output_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "results": {"type": "array"},
            "sources": {"type": "array"},
        },
    }
    effect_class = "read-only"  # type: ignore[assignment]
    cost_class = "medium"

    def __init__(self, rag: Any | None = None, provider: Any | None = None) -> None:
        # Explicit rag wins (tests, direct construction); otherwise the
        # module-global lifespan singleton resolves at execution time.
        self._rag = rag
        self._provider = provider

    def bind_rag(self, rag: Any) -> None:
        """Direct binding for the worker and non-factory construction."""
        self._rag = rag

    def _apply_relevance_filter(
        self, query: str, results: list[dict], cancel_event=None
    ) -> tuple[list[dict] | None, str]:
        """LLM relevance filter over retrieved results.

        Returns (subset, "filtered") when the model kept a strict, non-empty
        subset, else (None, reason) meaning "keep unfiltered". Never raises —
        no provider, timeouts, malformed output, or nothing mapped all fall
        back to the full results, so filtering is evidence-preserving in the
        worst case.
        """
        if not self._provider:
            return None, "no_provider"
        try:
            formatted = format_context_for_llm({"results": results})
            raw = self._provider.generate_structured(
                model=getattr(settings, "ollama_default_model", "qwen2.5:14b"),
                messages=[
                    {"role": "system", "content": _FILTER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Query: {query}\n\nRetrieved chunks:\n{formatted}"
                        ),
                    },
                ],
                schema=_FILTER_SCHEMA,
                temperature=0,
                timeout_ms=settings.planner_timeout_ms,
                cancel_event=cancel_event,
            )
        except Exception as e:  # noqa: BLE001 - filter must never fail retrieval
            logger.warning(
                "relevance filter unavailable, returning unfiltered results: %s", e
            )
            return None, "filter_failed"
        quotes = raw.get("quotes", []) if isinstance(raw, dict) else []
        subset = _map_quotes_to_results(quotes, results)
        if not subset:
            return None, "nothing_mapped"
        if len(subset) >= len(results):
            # The model kept everything: unfiltered either way.
            return None, "kept_all"
        return subset, "filtered"

    def _corpus_probe(
        self, notebook_id: str, file_id: str | None, file_name: str | None
    ) -> dict:
        """Ingestion snapshot for the `rag.corpus` trace span. Never raises."""
        try:
            rag = self._rag if self._rag is not None else get_rag_singleton()
        except RuntimeError as e:
            return {"probe": "unbound", "error": str(e)[:200]}
        probe = getattr(rag, "corpus_stats", None)
        if not callable(probe):
            # Legacy doubles predate corpus_stats: retrieval still works,
            # there is just no ingestion snapshot to report.
            return {"probe": "unavailable"}
        try:
            stats = probe(str(notebook_id), file_id=file_id, file_name=file_name)
        except Exception as e:  # noqa: BLE001 - probe must never fail retrieval
            return {"probe": "failed", "error": str(e)[:200]}
        return stats if isinstance(stats, dict) else {"probe": "malformed"}

    def _maybe_filter(
        self, query: str, results: list[dict], verbatim: bool, cancel_event=None
    ) -> tuple[list[dict], bool, str]:
        """Apply the relevance filter when requested and worthwhile.

        Returns (results_to_use, filtered, reason). `verbatim=True` skips
        filtering (L2 builders ground on full text); small outputs skip it
        too. Never raises — failures keep the unfiltered results.
        """
        with manual_span(
            "rag.filter",
            input=truncate(
                {
                    "query": query,
                    "results": len(results or []),
                    "verbatim": verbatim,
                },
                500,
            ),
            trace_context=get_trace_context(),
        ) as obs:
            out = self._do_maybe_filter(query, results, verbatim, cancel_event)
            kept, was_filtered, reason = out
            obs.update(
                output=truncate(
                    {
                        "filtered": was_filtered,
                        "reason": reason,
                        "kept": len(kept or []),
                        "total": len(results or []),
                    }
                )
            )
            return out

    def _do_maybe_filter(
        self, query: str, results: list[dict], verbatim: bool, cancel_event=None
    ) -> tuple[list[dict], bool, str]:
        if verbatim:
            return results, False, "not_requested"
        if not results:
            return results, False, "no_results"
        try:
            probe = format_context_for_llm({"results": results})
        except Exception:  # noqa: BLE001 - filter must never fail retrieval
            return results, False, "filter_failed"
        if len(probe or "") <= _FILTER_THRESHOLD_CHARS:
            return results, False, "under_threshold"
        subset, reason = self._apply_relevance_filter(
            query, results, cancel_event
        )
        if subset is None:
            return results, False, reason
        return subset, True, "filtered"

    def execute(self, request: ToolRequest) -> ToolResponse:
        notebook_id = request.input.get("notebook_id")
        if not notebook_id:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'notebook_id' is required in input (injected by the orchestrator, never the LLM)",
            )
        # `query` is the contract name; `message` stays accepted so plans
        # written against the Athena-era schema still execute.
        query = request.input.get("query") or request.input.get("message")
        if not query:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'query' is required in input",
            )
        try:
            top_k = int(request.input.get("top_k", _DEFAULT_TOP_K))
        except (TypeError, ValueError):
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'top_k' must be an integer",
            )
        raw_file_id = request.input.get("file_id")
        file_id = str(raw_file_id).strip() if isinstance(raw_file_id, str) else None
        if file_id is not None and not file_id:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'file_id' must be a non-empty snapshot id when provided",
            )
        if file_id is not None and "{{" in file_id:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'file_id' must be a literal snapshot id, never a {{id}} placeholder",
            )
        raw_file_name = request.input.get("file_name")
        file_name = (
            str(raw_file_name).strip() if isinstance(raw_file_name, str) else None
        )
        if file_name is not None and not file_name:
            file_name = None
        raw_mode = request.input.get("mode", "specific")
        mode = str(raw_mode).strip().lower() if raw_mode is not None else "specific"
        if mode not in _VALID_MODES:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'mode' must be one of ['overview', 'specific']",
            )
        # `verbatim=true` skips the LLM relevance filter and always returns
        # every chunk (L2 builders ground their reasoning steps on full text
        # via {{id}} placeholders). Default (false) filters large results
        # down to query-relevant chunks quoted verbatim.
        verbatim = bool(request.input.get("verbatim", False))
        # Trace parenting for the spans below: this body runs on the worker
        # thread with the caller-owned `tool:rag.query` span current, so
        # children nest under it. None (disabled / unit tests) makes every
        # manual_span a no-op — instrumentation never branches behavior.
        tool_ctx = get_trace_context()
        # Ingestion snapshot first: when retrieval comes back empty, this
        # span tells empty-corpus (ingestion) apart from killed-by-threshold
        # (tuning) without a second trip to the database.
        with manual_span(
            "rag.corpus",
            input=truncate(
                {
                    "notebook_id": str(notebook_id),
                    "file_id": file_id,
                    "file_name": file_name,
                    "mode": mode,
                    "top_k": top_k,
                }
            ),
            trace_context=tool_ctx,
        ) as corpus_obs:
            corpus = self._corpus_probe(str(notebook_id), file_id, file_name)
            corpus_obs.update(output=truncate(corpus))
        # Whole-file shortcut: when the scoped file(s) fit the context window,
        # return every chunk and skip BOTH the sub-query planner LLM call and
        # the embed -> vector -> FTS -> RRF -> rerank pipeline. Returns None
        # (over budget / empty / DB error / legacy double without the method),
        # which falls through to the ranked path below unchanged.
        whole_file = None
        wholefile_hit = False
        with manual_span(
            "rag.wholefile",
            input=truncate(
                {
                    "notebook_id": str(notebook_id),
                    "file_id": file_id,
                    "file_name": file_name,
                }
            ),
            trace_context=tool_ctx,
        ) as wholefile_obs:
            try:
                resolved_rag = self._rag if self._rag is not None else get_rag_singleton()
                whole_file_fn = getattr(resolved_rag, "retrieve_whole_file", None)
                whole_file = (
                    whole_file_fn(
                        str(notebook_id),
                        file_id=file_id,
                        file_name=file_name,
                    )
                    if callable(whole_file_fn)
                    else None
                )
            except Exception as e:  # noqa: BLE001 - optimization must never fail retrieval
                logger.warning("whole-file shortcut unavailable, using ranked retrieval: %s", e)
                whole_file = None

            if whole_file:
                whole_file, was_filtered, filter_reason = self._maybe_filter(
                    str(query), whole_file, verbatim, request.cancel_event
                )
                sources = extract_sources({"results": whole_file})
                output = format_context_for_llm({"results": whole_file})
                wholefile_hit = True
                wholefile_obs.update(
                    output=truncate(
                        {
                            "hit": True,
                            "chunks": len(whole_file),
                            "filtered": was_filtered,
                            "filter_reason": filter_reason,
                        }
                    )
                )
            else:
                wholefile_obs.update(
                    output=truncate(
                        {
                            "hit": False,
                            "verdict": corpus.get("wholefile_verdict", "unknown"),
                            "detail": "ranked path below; eligibility in rag.corpus",
                        }
                    )
                )

        if wholefile_hit and whole_file is not None:
            update_current_span(
                metadata={
                    "retrieval": _retrieval_summary(
                        corpus=corpus,
                        wholefile_hit=True,
                        shard_counts=[],
                        merged_total=len(whole_file),
                        filtered=was_filtered,
                        filter_reason=filter_reason,
                        returned=len(whole_file),
                    )
                }
            )
            return ToolResponse(
                tool_id=self.tool_id,
                ok=True,
                output=output or "(no chunks retrieved)",
                data={
                    "results": whole_file,
                    "sources": sources,
                    "query": str(query),
                    # No planner call ran, so the request is its own only query.
                    "generated_queries": [str(query)],
                    "notebook_id": str(notebook_id),
                    "file_id": file_id,
                    "file_name": file_name,
                    "mode": mode,
                    "whole_file": True,
                    "filtered": was_filtered,
                    "filter_reason": filter_reason,
                },
            )
        # Generate sub-queries via LLM, always.
        generated_queries: list[str] = [str(query)]
        if self._provider:
            try:
                schema = {
                    "type": "object",
                    "properties": {
                        "queries": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["queries"],
                }
                sys_prompt = (
                    "You are a retrieval query planner for a Retrieval-Augmented Generation (RAG) system. "
                    "Decompose the user's request into 1-3 distinct, concise document search queries. "
                    "Rewrite queries ONLY for document retrieval; do not answer the user. "
                    "Preserve the original information need, important entities, concepts, attributes, "
                    "relationships, constraints, names, IDs, acronyms, and technical terms. "
                    "Add a small number of useful synonyms or terminology variants that may appear in documents. "
                    "Remove conversational filler. Do not invent facts, entities, technologies, dates, or assumptions. "
                    "Avoid excessive or unrelated keywords. Preserve all important parts of multi-part questions. "
                    "Queries do not need to be grammatically correct; optimize for retrieval. "
                    "Return ONLY the list of rewritten search queries, with no explanation, answer, labels, JSON, "
                    "markdown, or reasoning."
                )

                raw_q = self._provider.generate_structured(
                    model=getattr(settings, "ollama_default_model", "qwen2.5:14b"),
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": str(query)},
                    ],
                    schema=schema,
                    temperature=0,
                    cancel_event=request.cancel_event,
                    max_tokens=_SUBQUERY_MAX_TOKENS,
                )
                qs = raw_q.get("queries", []) if isinstance(raw_q, dict) else []
                qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()][:3]
                if qs:
                    generated_queries = qs
            except Exception as e:  # noqa: BLE001 - decomposition fallback keeps original query
                logger.warning(
                    "rag query generation failed, falling back to original query: %s", e
                )
        # Execute retrieval for each sub-query and merge results
        all_results: list[dict] = []
        seen = set()
        shard_counts: list[int] = []
        try:
            for i, sub_q in enumerate(generated_queries):
                with manual_span(
                    f"rag.shard:{i + 1}",
                    input=truncate(
                        {
                            "shard": sub_q,
                            "top_k": top_k,
                            "file_id": file_id,
                            "file_name": file_name,
                            "mode": mode,
                        },
                        500,
                    ),
                    trace_context=tool_ctx,
                ) as shard_obs:
                    collected: list = []
                    try:
                        sub_results = rag_query(
                            str(notebook_id),
                            sub_q,
                            top_k=top_k,
                            rag=self._rag,
                            file_id=file_id,
                            file_name=file_name,
                            mode=mode,
                            collect_stats=collected,
                        )
                    except RuntimeError:
                        # Unbound singleton / dead shard plumbing: fail the
                        # tool honestly via the outer handler, never as an
                        # empty ok=True merge.
                        raise
                    except Exception as e:  # noqa: BLE001 - one bad shard must not kill the merge
                        logger.warning("rag sub-query failed for '%s': %s", sub_q, e)
                        shard_obs.update(
                            output=truncate({"error": str(e)[:200], "results": 0})
                        )
                        shard_counts.append(0)
                        continue
                    stat = (
                        collected[0]
                        if collected and isinstance(collected[0], dict)
                        else {}
                    )
                    shard_obs.update(
                        output=truncate(
                            _shard_summary(stat, len(sub_results or []))
                        )
                    )
                    shard_counts.append(len(sub_results or []))
                for r in sub_results or []:
                    # simple dedupe by chunk text
                    key = r.get("chunk_text") or r.get("content")
                    if key and key in seen:
                        continue
                    seen.add(key)
                    all_results.append(r)
        except RuntimeError as e:
            logger.warning("rag.query unbound/failed: %s", e)
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error=str(e)
            )
        except Exception as e:
            logger.exception("rag.query retrieval failed")
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error=f"retrieval failed: {e}",
            )
        results = all_results
        merged_total = len(results)
        results, was_filtered, filter_reason = self._maybe_filter(
            str(query), results, verbatim, request.cancel_event
        )
        sources = extract_sources({"results": results})
        output = format_context_for_llm({"results": results})
        update_current_span(
            metadata={
                "retrieval": _retrieval_summary(
                    corpus=corpus,
                    wholefile_hit=False,
                    shard_counts=shard_counts,
                    merged_total=merged_total,
                    filtered=was_filtered,
                    filter_reason=filter_reason,
                    returned=len(results),
                )
            }
        )
        return ToolResponse(
            tool_id=self.tool_id,
            ok=True,
            output=output or "(no chunks retrieved)",
            data={
                "results": results,
                "sources": sources,
                "query": str(query),
                "generated_queries": generated_queries,
                "notebook_id": str(notebook_id),
                "file_id": file_id,
                "file_name": file_name,
                "mode": mode,
                "whole_file": False,
                "filtered": was_filtered,
                "filter_reason": filter_reason,
            },
        )
