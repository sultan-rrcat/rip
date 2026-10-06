"""Shared corpus-state seam for L2 builders and L3 ReAct.

Both the deterministic builders and the ReAct fallback need to tri-state
the notebook file snapshot (is retrieval provably useless?). This module
owns the snapshot parsing so neither consumer leaks into the other's
internals — previously `react.py` imported the private `_corpus_state`
from `builders.py` across the seam.
"""

from __future__ import annotations

import re

#: Snapshot lines look like "report.pdf [ready] id=abc123" (see
#: runs/manager._load_file_snapshot). Code files are marked
#: "script.py [ready:code] id=..." — stored on disk, never embedded.
_SNAPSHOT_FILE = re.compile(
    r"(.+?)\s*\[(ready(?::code)?|processing|uploading|error)\]\s*id=(\S+)"
)


def _snapshot_files(notebook_context: str | None) -> list[tuple[str, str, str]]:
    """Parse (name, status, file_id) triples out of the snapshot string."""
    if not notebook_context:
        return []
    # Strip the "N file(s): " count prefix the manager prepends: without
    # this the first filename parses as "1 file(s): memory.py" (observed
    # live in a coding step header).
    cleaned_snapshot = re.sub(
        r"^\s*\d+\s*file\(s\):\s*", "", notebook_context
    )
    cleaned = []
    for name, status, fid in _SNAPSHOT_FILE.findall(cleaned_snapshot):
        fid_clean = fid.strip().rstrip(";,")
        if fid_clean:
            cleaned.append((name.strip(), status.strip().lower(), fid_clean))
    return cleaned


def _ready_files(notebook_context: str | None) -> list[tuple[str, str]]:
    """Return (name, file_id) for ALL ready DOCUMENT files in snapshot order.

    Code files ([ready:code]) are excluded: they have no embeddings, so
    rag.query over them would provably return "(no chunks retrieved)".
    """
    return [
        (name, fid)
        for name, status, fid in _snapshot_files(notebook_context)
        if status == "ready" and fid
    ]


def _ready_docs(notebook_context: str | None) -> list[tuple[str, str]]:
    """Alias for document-ready files (see _ready_files)."""
    return _ready_files(notebook_context)


def _ready_code(notebook_context: str | None) -> list[tuple[str, str]]:
    """Return (name, file_id) for ready CODE files ([ready:code])."""
    return [
        (name, fid)
        for name, status, fid in _snapshot_files(notebook_context)
        if status == "ready:code" and fid
    ]


def get_corpus_state(notebook_context: str | None) -> str:
    """Tri-state the notebook file snapshot for empty-corpus guards.

    Returns one of:
    - "unknown": snapshot is None (DB failure) — caller should still try
      rag.query; the database is ground truth, not the snapshot.
    - "ready": at least one ready DOCUMENT file — document-grounded
      retrieval path. Code-only notebooks ([ready:code]) are NOT ready:
      they hold no embeddings.
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
    # Code files ([ready:code]) hold no embeddings: a code-only notebook
    # has nothing retrievable, so report "empty" (not "processing" — there
    # is nothing to wait for). A processing doc alongside code still waits.
    docs = [f for f in files if f[1] != "ready:code"]
    if not docs:
        return "empty"
    if any(status == "ready" and fid for _, status, fid in docs):
        return "ready"
    return "processing"
