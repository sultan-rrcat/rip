"""Retrieval diversity: per-file round-robin interleave (pure unit tests).

No DB, no models — covers the helper only. Live replay of the trace-2
queries (2 ready files, one-doc collapse) was verified manually against
the office backend before wiring it in.
"""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.rag.vector_rag import _interleave_by_source


def _chunk(source: str, text: str) -> dict:
    return {"text": text, "metadata": {"source": source}}


def test_single_file_unchanged() -> None:
    items = [_chunk("a.pdf", f"t{i}") for i in range(3)]
    assert _interleave_by_source(items) == items


def test_empty_unchanged() -> None:
    assert _interleave_by_source([]) == []


def test_round_robin_across_files() -> None:
    items = [
        _chunk("a.pdf", "a1"),
        _chunk("a.pdf", "a2"),
        _chunk("a.pdf", "a3"),
        _chunk("b.pdf", "b1"),
    ]
    merged = _interleave_by_source(items)
    assert [c["text"] for c in merged] == ["a1", "b1", "a2", "a3"]


def test_within_file_order_preserved() -> None:
    items = [
        _chunk("b.pdf", "b1"),
        _chunk("a.pdf", "a1"),
        _chunk("b.pdf", "b2"),
        _chunk("a.pdf", "a2"),
    ]
    merged = _interleave_by_source(items)
    assert [c["text"] for c in merged] == ["b1", "a1", "b2", "a2"]


def test_missing_metadata_groups_as_unknown() -> None:
    items = [{"text": "x"}, _chunk("a.pdf", "a1")]
    merged = _interleave_by_source(items)
    assert [c["text"] for c in merged] == ["x", "a1"]
