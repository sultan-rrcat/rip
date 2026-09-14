from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:  # heavy torch chain — never import at runtime
    from app.rag.vector_rag import VectorRAG  # noqa: F401 — type-checker/IDE only


def get_rag(request: Request) -> Any:  # VectorRAG at runtime, Any avoids torch import
    return request.app.state.rag
