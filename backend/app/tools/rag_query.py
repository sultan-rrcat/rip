"""rag.query tool — document retrieval over the lifespan VectorRAG singleton.

Locked contract (MERGE_PLAN Q6/Q30): ``rag.query(notebook_id, query, top_k=8)``
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
from app.services.chat import extract_sources, format_context_for_llm
from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.rag_query")

_DEFAULT_TOP_K = 4

_VALID_MODES = frozenset({"specific", "overview"})

# Module-global singleton slot. Bound at lifespan (Phase 4.2 main.py calls
# bind_rag_singleton(app.state.rag)) or directly in tests via RagQueryTool(rag=...).
_rag_singleton: Any | None = None


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
) -> list[dict]:
    """Search notebook documents; return chunk-level results with source metadata.

    Each result: {content, source (file name), section (H1>H2>H3 path),
    rerank_score}. ``file_id``/``file_name`` scope retrieval to one file
    (literals from the notebook snapshot, never LLM-invented); ``mode``
    is ``specific`` (topical rank) or ``overview`` (stratified one-per-H1
    sample in doc order). Raises RuntimeError when the singleton is unbound;
    VectorRAG retrieval errors propagate to the caller (the Tool converts
    them to ok=False).
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
        "returns topical ranking."
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
                )
                qs = raw_q.get("queries", []) if isinstance(raw_q, dict) else []
                qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()][:3]
                if qs:
                    generated_queries = qs
            except Exception as e:
                logger.warning(
                    "rag query generation failed, falling back to original query: %s", e
                )
        # Execute retrieval for each sub-query and merge results
        all_results: list[dict] = []
        seen = set()
        try:
            for sub_q in generated_queries:
                try:
                    sub_results = rag_query(
                        str(notebook_id),
                        sub_q,
                        top_k=top_k,
                        rag=self._rag,
                        file_id=file_id,
                        file_name=file_name,
                        mode=mode,
                    )
                except Exception as e:
                    logger.warning("rag sub-query failed for '%s': %s", sub_q, e)
                    continue
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
        sources = extract_sources({"results": results})
        output = format_context_for_llm({"results": results})
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
            },
        )
