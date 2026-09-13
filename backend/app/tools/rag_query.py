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
from typing import Any

from app.services.chat import extract_sources, format_context_for_llm
from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.rag_query")

_DEFAULT_TOP_K = 8

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
        raise RuntimeError(
            "VectorRAG singleton is not bound (lifespan has not run)"
        )
    return rag


def rag_query(
    notebook_id: str,
    query: str,
    top_k: int = _DEFAULT_TOP_K,
    *,
    rag: Any | None = None,
) -> list[dict]:
    """Search notebook documents; return chunk-level results with source metadata.

    Each result: {content, source (file name), section (H1>H2>H3 path),
    rerank_score}. Raises RuntimeError when the singleton is unbound;
    VectorRAG retrieval errors propagate to the caller (the Tool converts
    them to ok=False).
    """
    resolved = rag if rag is not None else get_rag_singleton()
    context = resolved.retrieve_context(notebook_id, query, top_k=top_k)
    results = context.get("results", [])
    if not isinstance(results, list):
        raise RuntimeError("VectorRAG returned a malformed context (no results list)")
    return results


class RagQueryTool(Tool):
    tool_id = "rag.query"
    name = "RAG Query"
    description = (
        "Search the notebook's documents (vector + full-text, BGE reranked) "
        "and return grounded chunks with source metadata."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string"},
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["notebook_id", "query"],
    }
    output_schema = {
        "type": "object",
        "properties": {
            "results": {"type": "array"},
            "sources": {"type": "array"},
        },
    }
    effect_class = "read-only"  # type: ignore[assignment]
    cost_class = "medium"

    def __init__(self, rag: Any | None = None) -> None:
        # Explicit rag wins (tests, direct construction); otherwise the
        # module-global lifespan singleton resolves at execution time.
        self._rag = rag

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
        try:
            results = rag_query(
                str(notebook_id), str(query), top_k=top_k, rag=self._rag
            )
        except RuntimeError as e:
            logger.warning("rag.query unbound/failed: %s", e)
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error=str(e)
            )
        except Exception as e:  # noqa: BLE001 - retrieval failure is a tool failure
            logger.exception("rag.query retrieval failed")
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error=f"retrieval failed: {e}",
            )
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
                "notebook_id": str(notebook_id),
            },
        )
