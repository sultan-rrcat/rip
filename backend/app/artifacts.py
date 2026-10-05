"""File-based artifacts (Q34 REWRITE — not Athena's inline base64).

Tool steps that produce files/charts carry their raw
`ToolResponse.data` dict forward on `StepResult.data`. This module converts
those payloads into FILES under:

    {upload_dir}/{notebook_id}/artifacts/{run_id}/{step_id}/{filename}

plus an `index.json` per run mapping `artifact_id -> {step_id, filename,
kind, mime}`. The SSE `artifacts` event carries only
`{artifact_id, kind, filename, url}` download links (never inline base64);
`GET /v1/runs/{run_id}/artifacts/{artifact_id}` serves the bytes.

Collection keys on DATA SHAPES, never on tool ids (same shapes as Athena):
- {"svg": "<svg...>"} → chart (image/svg+xml)
- {"docx_b64": ...} / {"pdf_b64": ...} → document
- {"markdown": ...} (only when it rides with binaries) → document (.md)
- {"rows": [...], "row_count": ...} → data (.json)

Naming: doc.convert files keep the source-file stem; doc.generate
reports are named after the report `title` slug (never the step id), and
only the LAST successful report per run is surfaced — earlier attempts
are superseded. A report's companion .md is skipped (its markdown
already renders in chat), so a pdf ask yields exactly one file.

Anything else (plain text answers, stdout dumps, RAG passages) is NOT an
artifact — it already travels via the step output / final answer.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from pathlib import Path

logger = logging.getLogger("artifacts")

MIME_SVG = "image/svg+xml"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_PDF = "application/pdf"
MIME_MARKDOWN = "text/markdown"
MIME_JSON = "application/json"

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

#: Max filename stem length for title-derived names (long report titles
#: would otherwise produce unwieldy filenames).
_TITLE_STEM_MAX = 80


def _safe(name: str) -> str:
    """Filesystem-safe segment (planner-generated ids are untrusted input)."""
    return _SAFE.sub("_", name).strip("._") or "file"


def _slug(title: str) -> str:
    """Title-derived stem: readable, bounded, never empty."""
    slug = _SAFE.sub("_", title.strip()).strip("._")
    if len(slug) > _TITLE_STEM_MAX:
        slug = slug[:_TITLE_STEM_MAX].rstrip("._")
    return slug or "report"


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _is_generate_report(result) -> bool:
    """True for successful doc.generate report steps (not doc.convert).

    doc.generate payloads carry the report `title`; doc.convert payloads
    carry `source_file_id`/`conversions` instead. Only one report per run
    is intended (the ReAct loop is told to generate once and answer from
    it), so earlier reports are superseded by later ones.
    """
    aid = getattr(result, "agent_id", "")
    if str(getattr(aid, "value", aid)) != "doc.generate":
        return False
    data = getattr(result, "data", None)
    return isinstance(data, dict) and isinstance(data.get("title"), str)


def collect_artifacts(
    step_results: list,
    *,
    upload_dir: str,
    notebook_id: str,
    run_id: str,
) -> list[dict]:
    """Persist tool outputs to disk; return SSE-ready artifact descriptors.

    Each descriptor is `{artifact_id, kind, filename, url}` with
    `url = /v1/runs/{run_id}/artifacts/{artifact_id}`. Returns [] when no
    step produced an artifact shape. Fail-soft per artifact: an undecodable
    payload is logged and skipped, never aborting the run.
    """
    run_dir = (
        Path(upload_dir) / str(notebook_id) / "artifacts" / str(run_id)
    )
    found: list[dict] = []
    # Content hashes of charts already collected: identical SVG bytes
    # (trace 07fb4f59 r2/r3 plotted the same data twice) would otherwise
    # surface the same plot twice in the frontend Artifacts panel.
    seen_charts: set[str] = set()
    # doc.generate reports are last-wins: a run that generated twice (junk
    # first attempt, corrected second) must surface only the final report,
    # not every intermediate (r1.pdf + r3.pdf side by side).
    last_report_idx: int | None = None
    for i, result in enumerate(step_results):
        if getattr(result.status, "value", result.status) != "success":
            continue
        if _is_generate_report(result):
            last_report_idx = i
    for i, result in enumerate(step_results):
        if getattr(result.status, "value", result.status) != "success":
            continue
        if _is_generate_report(result) and i != last_report_idx:
            logger.info(
                "artifact superseded report skipped run=%s step=%s",
                run_id, getattr(result, "step_id", "?"),
            )
            continue
        data = getattr(result, "data", None) or {}
        if not isinstance(data, dict):
            continue
        step_id = _safe(str(getattr(result, "step_id", "step")))
        found.extend(
            _collect_from_data(
                data, run_dir=run_dir, run_id=str(run_id), step_id=step_id,
                seen_charts=seen_charts,
            )
        )
    if found:
        _write_index(run_dir, found)
    return found


def _collect_from_data(
    data: dict, *, run_dir: Path, run_id: str, step_id: str,
    seen_charts: set[str] | None = None,
) -> list[dict]:
    out: list[dict] = []

    # doc.convert convert-all: one entry per source file.
    conversions = data.get("conversions")
    if isinstance(conversions, list) and conversions:
        for entry in conversions:
            if isinstance(entry, dict):
                out.extend(
                    _collect_from_data(
                        entry, run_dir=run_dir, run_id=run_id, step_id=step_id,
                        seen_charts=seen_charts,
                    )
                )
        return out

    def _stem() -> str:
        """Filename stem: source file name, then report title, else step id."""
        raw = data.get("source_file_name")
        if isinstance(raw, str) and raw.strip():
            stem = raw.strip().rsplit(".", 1)[0]
            return _safe(stem)
        title = data.get("title")
        if isinstance(title, str) and title.strip():
            return _slug(title)
        return _safe(step_id)

    def _is_report() -> bool:
        """True for doc.generate payloads (title, no conversion markers)."""
        return (
            isinstance(data.get("title"), str)
            and bool(data.get("title").strip())
            and not isinstance(data.get("source_file_id"), str)
            and not isinstance(data.get("conversions"), list)
        )

    def _filename(ext: str) -> str:
        # Reports are named after their title alone ("r1.pdf" tells the user
        # nothing); per-step directories already isolate same-named files.
        if _is_report():
            return f"{_stem()}.{ext}"
        stem = _stem()
        if stem == _safe(step_id):
            return f"{stem}.{ext}"
        return f"{_safe(step_id)}_{stem}.{ext}"

    def add(kind: str, mime: str, filename: str, content: bytes) -> None:
        artifact_id = uuid.uuid4().hex
        try:
            _write_bytes(run_dir / step_id / filename, content)
        except OSError:
            logger.exception("artifact write failed run=%s step=%s", run_id, step_id)
            return
        out.append(
            {
                "artifact_id": artifact_id,
                "kind": kind,
                "mime": mime,
                "step_id": step_id,
                "filename": filename,
                "url": f"/v1/runs/{run_id}/artifacts/{artifact_id}",
            }
        )

    svg = data.get("svg")
    if isinstance(svg, str) and svg.lstrip().startswith("<svg"):
        import hashlib as _hashlib

        digest = _hashlib.sha256(svg.encode("utf-8")).hexdigest()
        if seen_charts is not None:
            if digest in seen_charts:
                logger.info(
                    "artifact duplicate chart skipped run=%s step=%s",
                    run_id, step_id,
                )
            else:
                seen_charts.add(digest)
                add("chart", MIME_SVG, f"{step_id}.svg", svg.encode("utf-8"))
        else:
            add("chart", MIME_SVG, f"{step_id}.svg", svg.encode("utf-8"))

    docx_b64 = data.get("docx_b64")
    if isinstance(docx_b64, str) and docx_b64:
        try:
            add("document", MIME_DOCX, _filename("docx"), base64.b64decode(docx_b64))
        except ValueError:
            logger.warning("artifact docx_b64 undecodable run=%s step=%s", run_id, step_id)

    pdf_b64 = data.get("pdf_b64")
    if isinstance(pdf_b64, str) and pdf_b64:
        try:
            add("document", MIME_PDF, _filename("pdf"), base64.b64decode(pdf_b64))
        except ValueError:
            logger.warning("artifact pdf_b64 undecodable run=%s step=%s", run_id, step_id)

    markdown = data.get("markdown")
    has_binary = ("docx_b64" in data or "pdf_b64" in data)
    if isinstance(markdown, str) and markdown and has_binary and not _is_report():
        # Only a file artifact when it rides with rendered binaries; a bare
        # markdown string is just step output — EXCEPT doc.convert output,
        # which is a verbatim file conversion (source_file_id marks it).
        # doc.generate reports skip the companion .md: the markdown already
        # renders in chat, so a pdf ask would otherwise surface two files.
        add("document", MIME_MARKDOWN, _filename("md"), markdown.encode("utf-8"))
    elif (
        isinstance(markdown, str)
        and markdown
        and isinstance(data.get("source_file_id"), str)
    ):
        add("document", MIME_MARKDOWN, _filename("md"), markdown.encode("utf-8"))

    rows = data.get("rows")
    if isinstance(rows, list) and "row_count" in data:
        add("data", MIME_JSON, f"{step_id}.json", json.dumps(rows).encode("utf-8"))

    return out


def _write_index(run_dir: Path, artifacts: list[dict]) -> None:
    index = {
        a["artifact_id"]: {
            "step_id": a["step_id"],
            "filename": a["filename"],
            "kind": a["kind"],
            "mime": a["mime"],
        }
        for a in artifacts
    }
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
    except OSError:
        logger.exception("artifact index write failed dir=%s", run_dir)


def resolve_artifact(
    *, upload_dir: str, notebook_id: str, run_id: str, artifact_id: str
) -> tuple[Path, str, str] | None:
    """Resolve an artifact_id to (path, filename, mime); None when unknown.

    Used by `GET /v1/runs/{run_id}/artifacts/{artifact_id}`. The lookup is
    confined to the run's own directory (index.json), so one run can never
    address another run's files — no path traversal via crafted ids.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", artifact_id or ""):
        return None
    run_dir = Path(upload_dir) / str(notebook_id) / "artifacts" / str(run_id)
    try:
        index = json.loads((run_dir / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = index.get(artifact_id)
    if not isinstance(entry, dict):
        return None
    step_id = _safe(str(entry.get("step_id", "")))
    filename = _safe(str(entry.get("filename", "")))
    path = run_dir / step_id / filename
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    mime = str(entry.get("mime") or "application/octet-stream")
    return path, filename, mime


__all__ = [
    "collect_artifacts",
    "resolve_artifact",
]
