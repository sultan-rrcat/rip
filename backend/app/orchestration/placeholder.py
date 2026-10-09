"""Shared placeholder + fenced-code scanning helpers.

Fenced code blocks (```...```) carry literal file content inlined by the
L2 CODE builder — `{{...}}` patterns inside them are source text (Jinja
templates, f-string docs, placeholder examples), never DAG wiring. All
placeholder scans must strip fenced spans first (trace 987e6ceb:
plan_graph.py's own source contains {{2}}/{{id}}/{{step_id}} and the
validator rejected the whole plan as ungrounded).

This module is the single source of truth for both the fenced-code
regex and the placeholder regex. Before this consolidation, each of
validator.py, plan.py, plan_graph.py, and aggregator.py carried its
own copy of the fenced-code regex — a change had to be made in four
places.
"""

from __future__ import annotations

import re

#: Matches {{step_id}} placeholders (DAG wiring between steps).
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_-]+)\s*\}\}")

#: Fenced code blocks — stripped before any placeholder scan so literal
#: `{{...}}` source text is never wired (see module docstring).
FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)


def strip_fenced_code(text: str) -> str:
    """Remove fenced code spans so literal `{{...}}` text is not wired."""
    return FENCED_CODE.sub("", text)


def placeholders_outside_code(value: object) -> set[str]:
    """Collect {{id}} refs found OUTSIDE fenced code blocks.

    Recursively walks dicts, lists, and strings. Used by the Validator
    (grounding checks), Plan.from_model (auto-wire), and the Aggregator
    (unresolved-placeholder detection).
    """
    found: set[str] = set()
    if isinstance(value, str):
        found.update(PLACEHOLDER.findall(strip_fenced_code(value)))
    elif isinstance(value, dict):
        for v in value.values():
            found.update(placeholders_outside_code(v))
    elif isinstance(value, list):
        for v in value:
            found.update(placeholders_outside_code(v))
    return found


def has_unresolved_placeholder(output: str | None) -> bool:
    """True when output contains {{...}} outside fenced code blocks."""
    return bool(PLACEHOLDER.search(strip_fenced_code(output or "")))
