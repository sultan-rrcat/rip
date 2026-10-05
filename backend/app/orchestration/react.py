"""L3 ReAct — compatibility shim over `react_engine`.

The implementation lives on `ReActEngine` in `react_engine.py`. This
module re-exports the public names so existing callers (orchestrator,
tests) are unaffected.
"""

from __future__ import annotations

from app.orchestration.react_engine import (
    _ANSWER_FALLBACK_FIELDS,
    _OVERVIEW_HINTS,
    _TOOL_INPUT_HINTS,
    _TOOL_OUTPUT_TYPES,
    MAX_PLOT_CHARTS_PER_RUN,
    MAX_REACT_ITERATIONS,
    REACT_SCHEMA,
    ReActEngine,
    ReactResult,
    _action_signature,
    _chart_observation,
    _default_doc_format,
    _default_react_mode,
    _doc_content_key,
    _fallback_answer_text,
    _normalize_react_input,
    _output_type,
    _plot_data_key,
    _synthesis_evidence_line,
    _validate_react_input,
    run_react,
)

__all__ = [
    "MAX_PLOT_CHARTS_PER_RUN",
    "MAX_REACT_ITERATIONS",
    "REACT_SCHEMA",
    "_ANSWER_FALLBACK_FIELDS",
    "_OVERVIEW_HINTS",
    "_TOOL_INPUT_HINTS",
    "_TOOL_OUTPUT_TYPES",
    "ReActEngine",
    "ReactResult",
    "_action_signature",
    "_chart_observation",
    "_default_doc_format",
    "_default_react_mode",
    "_doc_content_key",
    "_fallback_answer_text",
    "_normalize_react_input",
    "_output_type",
    "_plot_data_key",
    "_synthesis_evidence_line",
    "_validate_react_input",
    "run_react",
]
