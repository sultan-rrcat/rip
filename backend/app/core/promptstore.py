"""Prompt + tool-config store: DB source of truth, in-memory write-through.

Contract (mirrors app/core/runtime.py):
- Precedence: DB row > code seed. Code seeds are the fallback when no row
  exists — a fresh bootstrap with an empty DB still runs.
- The module dicts `_prompts` / `_tools` are the read cache: hot paths
  (`get_prompt`, `get_tool_description`, `is_tool_enabled`) are pure dict
  lookups with zero DB round-trips. PUT writes DB first, then memory
  (strong consistency in this process — not eventual).
- `load_store()` + `ensure_seeds()` run once in lifespan BEFORE the /v1
  runtime is composed. Never raise — degraded boot keeps code seeds.
- Templated prompts (router/react) carry {placeholders}; `set_prompt`
  refuses (ValueError → 422) bodies missing a required slot.
- Cache seam for the future Redis move: all reads go through
  `_cache_get`/`_cache_set`/`_cache_invalidate` on the module backend.
  Today that backend is the in-memory dict; swapping in Redis changes
  only this module, never the call sites.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

logger = logging.getLogger("core.promptstore")

Source = Literal["seed", "db"]

MAX_PROMPT_LEN = 20000
MAX_FRAGMENT_LEN = 4000
MAX_DESCRIPTION_LEN = 4000

#: Required {placeholders} per templated prompt. PUT refuses bodies
#: missing any of these (422 with the missing slot named).
REQUIRED_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "router.system_prompt": ("{intents_block}", "{corpus_section}"),
    "router.corpus_suffix": ("{corpus_hint}",),
    "react.system_prompt": ("{agent_ids}", "{tool_ids}", "{notebook_context}"),
}

#: Max body length per prompt key (fragments/descriptions are short).
MAX_LEN: dict[str, int] = {
    "builders.presentation_suffix": MAX_FRAGMENT_LEN,
    "builders.empty_answer_guidance": MAX_FRAGMENT_LEN,
    "agent.reasoning.description": MAX_DESCRIPTION_LEN,
    "agent.coding.description": MAX_DESCRIPTION_LEN,
}

TOOL_IDS: tuple[str, ...] = (
    "rag.query",
    "plot.chart",
    "doc.generate",
    "doc.convert",
    "notebook.inspect",
    "code.read",
    "code.sandbox",
)


@dataclass(frozen=True)
class PromptGroup:
    id: str
    title: str
    description: str


PROMPT_GROUPS: tuple[PromptGroup, ...] = (
    PromptGroup("agents", "Agent Voices",
                "System prompts + menu descriptions for the two agents."),
    PromptGroup("router", "Intent Router",
                "L1 classification prompt (templated) + corpus-hint suffix."),
    PromptGroup("react", "ReAct Loop",
                "L3 per-turn planner prompt (templated with the live tool menu)."),
    PromptGroup("retrieval", "Retrieval",
                "LLM relevance-filter voice inside rag.query."),
    PromptGroup("builders", "Answer Fragments",
                "Style + honesty fragments spliced into L2 builder messages."),
)

#: key -> (group, label, description)
PROMPT_SPECS: dict[str, tuple[str, str, str]] = {
    "agent.reasoning.system_prompt": (
        "agents", "Reasoning voice",
        "User-facing voice for chat, grounded QA, compare/summarize/quiz writers, ReAct synthesis."),
    "agent.reasoning.description": (
        "agents", "Reasoning description",
        "Menu text for the reasoning agent (shapes planner routing)."),
    "agent.coding.system_prompt": (
        "agents", "Coding voice",
        "Voice for code generation / explanation (generate-and-present only)."),
    "agent.coding.description": (
        "agents", "Coding description",
        "Menu text for the coding agent (shapes planner routing)."),
    "router.system_prompt": (
        "router", "Router template",
        "Requires {intents_block} (rendered from the Intent enum) and {corpus_section}."),
    "router.corpus_suffix": (
        "router", "Corpus-hint suffix",
        "Appended when a notebook hint exists. Requires {corpus_hint}."),
    "react.system_prompt": (
        "react", "ReAct template",
        "Requires {agent_ids}, {tool_ids} (enabled tools only), {notebook_context}."),
    "rag.filter_prompt": (
        "retrieval", "Relevance filter",
        "Keeps only query-relevant chunks, quoted verbatim."),
    "builders.presentation_suffix": (
        "builders", "Presentation suffix",
        "Styles HOW the honest answer reads (after grounding)."),
    "builders.empty_answer_guidance": (
        "builders", "Empty-answer guidance",
        "Demands complete honest sentences when chunks lack coverage."),
}


@lru_cache(maxsize=1)
def _seed_map() -> dict[str, str]:
    """Code seeds, collected lazily (module-level import would cycle)."""
    from app.agents.coding import CODING_DESCRIPTION, CODING_SYSTEM_PROMPT
    from app.agents.reasoning import REASONING_DESCRIPTION, REASONING_SYSTEM_PROMPT
    from app.orchestration.builders import (
        _EMPTY_ANSWER_GUIDANCE_SEED,
        _PRESENTATION_SUFFIX_SEED,
    )
    from app.orchestration.react_loop import REACT_PROMPT_TEMPLATE
    from app.orchestration.router import (
        ROUTER_CORPUS_SUFFIX_TEMPLATE,
        ROUTER_PROMPT_TEMPLATE,
    )
    from app.tools.rag_query import _FILTER_SYSTEM_PROMPT

    return {
        "agent.reasoning.system_prompt": REASONING_SYSTEM_PROMPT,
        "agent.reasoning.description": REASONING_DESCRIPTION,
        "agent.coding.system_prompt": CODING_SYSTEM_PROMPT,
        "agent.coding.description": CODING_DESCRIPTION,
        "router.system_prompt": ROUTER_PROMPT_TEMPLATE,
        "router.corpus_suffix": ROUTER_CORPUS_SUFFIX_TEMPLATE,
        "react.system_prompt": REACT_PROMPT_TEMPLATE,
        "rag.filter_prompt": _FILTER_SYSTEM_PROMPT,
        "builders.presentation_suffix": _PRESENTATION_SUFFIX_SEED,
        "builders.empty_answer_guidance": _EMPTY_ANSWER_GUIDANCE_SEED,
    }


@lru_cache(maxsize=1)
def _tool_seed_descriptions() -> dict[str, str]:
    """Tool description seeds from the tool classes (no instantiation)."""
    from app.tools.code_read import CodeReadTool
    from app.tools.code_sandbox import CodeSandboxTool
    from app.tools.doc_convert import DocConvertTool
    from app.tools.doc_generate import DocGenerateTool
    from app.tools.notebook_inspect import NotebookInspectTool
    from app.tools.plot_chart import PlotChartTool
    from app.tools.rag_query import RagQueryTool

    return {
        "rag.query": RagQueryTool.description,
        "plot.chart": PlotChartTool.description,
        "doc.generate": DocGenerateTool.description,
        "doc.convert": DocConvertTool.description,
        "notebook.inspect": NotebookInspectTool.description,
        "code.read": CodeReadTool.description,
        "code.sandbox": CodeSandboxTool.description,
    }


@lru_cache(maxsize=1)
def _tool_meta() -> dict[str, dict[str, Any]]:
    """Static per-tool metadata (name/effect/cost stay code-owned)."""
    from app.tools.code_read import CodeReadTool
    from app.tools.code_sandbox import CodeSandboxTool
    from app.tools.doc_convert import DocConvertTool
    from app.tools.doc_generate import DocGenerateTool
    from app.tools.notebook_inspect import NotebookInspectTool
    from app.tools.plot_chart import PlotChartTool
    from app.tools.rag_query import RagQueryTool

    out: dict[str, dict[str, Any]] = {}
    for cls in (RagQueryTool, PlotChartTool, DocGenerateTool,
                DocConvertTool, NotebookInspectTool, CodeReadTool,
                CodeSandboxTool):
        out[cls.tool_id] = {
            "name": cls.name,
            "effect_class": cls.effect_class,
            "cost_class": cls.cost_class,
        }
    return out


# ─── Cache seam (in-memory today, Redis later) ────────────────────────────────
# All reads/writes below go through these three functions. A Redis move
# replaces their bodies (plus load_store/ensure_seeds persistence) without
# touching any call site: get_prompt / is_tool_enabled keep their sync
# signatures because the hot data stays process-local either way.

_prompts: dict[str, str] = {}
_tools: dict[str, dict[str, Any]] = {}


def _cache_get_prompt(key: str) -> str | None:
    return _prompts.get(key)


def _cache_set_prompt(key: str, body: str) -> None:
    _prompts[key] = body


def _cache_invalidate_prompt(key: str) -> None:
    _prompts.pop(key, None)


def _cache_get_tool(tool_id: str) -> dict[str, Any] | None:
    row = _tools.get(tool_id)
    return dict(row) if row is not None else None


def _cache_set_tool(tool_id: str, row: dict[str, Any]) -> None:
    _tools[tool_id] = dict(row)


# ─── Reads (hot path: pure memory, never DB) ──────────────────────────────────

def get_prompt(key: str) -> str:
    """Prompt body: DB override when loaded, else the code seed."""
    hit = _cache_get_prompt(key)
    if hit is not None:
        return hit
    return _seed_map()[key]


def get_tool_description(tool_id: str) -> str:
    """Effective tool description: admin override or the class docstring."""
    row = _cache_get_tool(tool_id)
    if row and row.get("description_override"):
        return row["description_override"]
    return _tool_seed_descriptions()[tool_id]


def is_tool_enabled(tool_id: str) -> bool:
    """Kill-switch read. Unknown ids default enabled (validator owns unknown)."""
    row = _cache_get_tool(tool_id)
    if row is None:
        return True
    return bool(row.get("enabled", True))


# ─── Boot (lifespan, once) ────────────────────────────────────────────────────

def ensure_seeds() -> None:
    """Insert missing prompt/tool rows (never overwrite admin edits)."""
    from app.core.db import pg_connection

    with pg_connection() as conn, conn.cursor() as cur:
        for key, body in _seed_map().items():
            cur.execute(
                """INSERT INTO runtime_prompts (key, body)
                   VALUES (%s, %s) ON CONFLICT (key) DO NOTHING""",
                (key, body),
            )
        for tool_id in TOOL_IDS:
            cur.execute(
                    """INSERT INTO tool_config (tool_id, enabled)
                       VALUES (%s, TRUE) ON CONFLICT (tool_id) DO NOTHING""",
                (tool_id,),
            )


def load_store() -> tuple[int, int]:
    """Load DB rows into memory. Returns (prompts, tools). Never raises."""
    from app.core.db import pg_connection

    n_prompts = n_tools = 0
    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT key, body FROM runtime_prompts")
            for key, body in cur.fetchall():
                if key in PROMPT_SPECS:
                    _cache_set_prompt(key, body)
                    n_prompts += 1
            cur.execute(
                "SELECT tool_id, enabled, description_override FROM tool_config")
            for tool_id, enabled, override in cur.fetchall():
                if tool_id in TOOL_IDS:
                    _cache_set_tool(tool_id, {
                        "enabled": bool(enabled),
                        "description_override": override,
                    })
                    n_tools += 1
    except Exception as e:  # noqa: BLE001 - degraded boot keeps code seeds
        logger.warning("prompt store load skipped (DB down: %s)", e)
    return n_prompts, n_tools


# ─── Writes (validate all, DB first, memory after) ────────────────────────────

def _check_body(key: str, body: Any) -> str:
    if not isinstance(body, str) or not body.strip():
        raise ValueError(f"{key}: must be a non-empty string")
    text = body.strip()
    limit = MAX_LEN.get(key, MAX_PROMPT_LEN)
    if len(text) > limit:
        raise ValueError(f"{key}: exceeds {limit} characters ({len(text)})")
    missing = [p for p in REQUIRED_PLACEHOLDERS.get(key, ()) if p not in text]
    if missing:
        raise ValueError(
            f"{key}: missing required placeholder(s): {', '.join(missing)}")
    return text


def set_prompt(key: str, body: Any, *, actor: str | None = None) -> str:
    if key not in PROMPT_SPECS:
        raise LookupError(f"unknown prompt key: {key}")
    text = _check_body(key, body)
    from app.core.db import pg_connection

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO runtime_prompts (key, body, updated_by, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                   SET body = EXCLUDED.body, updated_by = EXCLUDED.updated_by,
                       updated_at = NOW()""",
                (key, text, actor),
            )
    except Exception as e:  # noqa: BLE001 - DB outage surfaces as 503
        logger.warning("prompt PUT failed (DB): %s", e)
        raise RuntimeError(f"prompt store unavailable: {e}")
    _cache_set_prompt(key, text)
    logger.info("prompt updated by %s: %s", actor or "admin", key)
    return text


def reset_prompts(keys: list[str] | None = None) -> list[str]:
    targets = list(keys) if keys else sorted(PROMPT_SPECS)
    for key in targets:
        if key not in PROMPT_SPECS:
            raise LookupError(f"unknown prompt key: {key}")
    from app.core.db import pg_connection

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            if keys:
                cur.execute(
                    "DELETE FROM runtime_prompts WHERE key = ANY(%s)", (targets,))
            else:
                cur.execute("DELETE FROM runtime_prompts")
    except Exception as e:  # noqa: BLE001 - DB outage surfaces as 503
        logger.warning("prompt reset failed (DB): %s", e)
        raise RuntimeError(f"prompt store unavailable: {e}")
    for key in targets:
        _cache_invalidate_prompt(key)
    logger.info("prompts reset to seeds: %s", sorted(targets))
    return sorted(targets)


def set_tool_enabled(
    tool_id: str, enabled: Any, *, actor: str | None = None
) -> bool:
    if tool_id not in TOOL_IDS:
        raise LookupError(f"unknown tool: {tool_id}")
    if isinstance(enabled, bool):
        flag = enabled
    elif str(enabled).strip().lower() in ("true", "1", "yes", "on"):
        flag = True
    elif str(enabled).strip().lower() in ("false", "0", "no", "off"):
        flag = False
    else:
        raise ValueError(f"{tool_id}: enabled must be boolean")
    from app.core.db import pg_connection

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tool_config (tool_id, enabled, updated_by, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (tool_id) DO UPDATE
                   SET enabled = EXCLUDED.enabled,
                       updated_by = EXCLUDED.updated_by, updated_at = NOW()""",
                (tool_id, flag, actor),
            )
    except Exception as e:  # noqa: BLE001 - DB outage surfaces as 503
        logger.warning("tool toggle failed (DB): %s", e)
        raise RuntimeError(f"prompt store unavailable: {e}")
    row = _cache_get_tool(tool_id) or {}
    row["enabled"] = flag
    _cache_set_tool(tool_id, row)
    logger.info("tool %s %s by %s", tool_id,
                "enabled" if flag else "disabled", actor or "admin")
    return flag


def set_tool_description(
    tool_id: str, description: Any, *, actor: str | None = None
) -> str:
    if tool_id not in TOOL_IDS:
        raise LookupError(f"unknown tool: {tool_id}")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{tool_id}: description must be a non-empty string")
    text = description.strip()
    if len(text) > MAX_DESCRIPTION_LEN:
        raise ValueError(
            f"{tool_id}: description exceeds {MAX_DESCRIPTION_LEN} characters")
    from app.core.db import pg_connection

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tool_config (tool_id, enabled, description_override,
                                            updated_by, updated_at)
                   VALUES (%s, TRUE, %s, %s, NOW())
                   ON CONFLICT (tool_id) DO UPDATE
                   SET description_override = EXCLUDED.description_override,
                       updated_by = EXCLUDED.updated_by, updated_at = NOW()""",
                (tool_id, text, actor),
            )
    except Exception as e:  # noqa: BLE001 - DB outage surfaces as 503
        logger.warning("tool description PUT failed (DB): %s", e)
        raise RuntimeError(f"prompt store unavailable: {e}")
    row = _cache_get_tool(tool_id) or {"enabled": True}
    row["description_override"] = text
    _cache_set_tool(tool_id, row)
    return text


def reset_tools(tool_ids: list[str] | None = None) -> list[str]:
    targets = list(tool_ids) if tool_ids else list(TOOL_IDS)
    for tool_id in targets:
        if tool_id not in TOOL_IDS:
            raise LookupError(f"unknown tool: {tool_id}")
    from app.core.db import pg_connection

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            if tool_ids:
                cur.execute(
                    "DELETE FROM tool_config WHERE tool_id = ANY(%s)", (targets,))
            else:
                cur.execute("DELETE FROM tool_config")
    except Exception as e:  # noqa: BLE001 - DB outage surfaces as 503
        logger.warning("tool reset failed (DB): %s", e)
        raise RuntimeError(f"prompt store unavailable: {e}")
    for tool_id in targets:
        _cache_set_tool(tool_id, {"enabled": True,
                                  "description_override": None})
    return sorted(targets)


# ─── Admin snapshot ───────────────────────────────────────────────────────────

def _prompt_meta() -> dict[str, dict[str, Any]]:
    from app.core.db import pg_connection

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT key, updated_by, updated_at FROM runtime_prompts")
            return {k: {"updated_by": by,
                        "updated_at": at.isoformat() if at else None}
                    for k, by, at in cur.fetchall()}
    except Exception as e:  # noqa: BLE001 - GET degrades honest when DB is down
        logger.warning("prompt snapshot meta unreadable (%s)", e)
        return {}


def _tool_meta_rows() -> dict[str, dict[str, Any]]:
    from app.core.db import pg_connection

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tool_id, updated_by, updated_at FROM tool_config")
            return {t: {"updated_by": by,
                        "updated_at": at.isoformat() if at else None}
                    for t, by, at in cur.fetchall()}
    except Exception as e:  # noqa: BLE001 - GET degrades honest when DB is down
        logger.warning("tool snapshot meta unreadable (%s)", e)
        return {}


def snapshot() -> dict[str, Any]:
    seeds = _seed_map()
    seed_desc = _tool_seed_descriptions()
    static_meta = _tool_meta()
    prompt_meta = _prompt_meta()
    tool_rows = _tool_meta_rows()
    prompts: dict[str, Any] = {}
    for key, (group, label, description) in PROMPT_SPECS.items():
        override = _cache_get_prompt(key)
        prompts[key] = {
            "key": key, "group": group, "label": label,
            "description": description, "value": get_prompt(key),
            "seed": seeds[key], "source": "db" if override is not None else "seed",
            "placeholders": list(REQUIRED_PLACEHOLDERS.get(key, ())),
            "updated_by": (prompt_meta.get(key) or {}).get("updated_by"),
            "updated_at": (prompt_meta.get(key) or {}).get("updated_at"),
        }
    tools: dict[str, Any] = {}
    for tool_id in TOOL_IDS:
        row = _cache_get_tool(tool_id) or {}
        override = row.get("description_override")
        tools[tool_id] = {
            "tool_id": tool_id, **static_meta[tool_id],
            "enabled": bool(row.get("enabled", True)),
            "description": override or seed_desc[tool_id],
            "description_source": "db" if override else "seed",
            "updated_by": (tool_rows.get(tool_id) or {}).get("updated_by"),
            "updated_at": (tool_rows.get(tool_id) or {}).get("updated_at"),
        }
    return {
        "groups": [{"id": g.id, "title": g.title, "description": g.description,
                    "prompts": [k for k, (gr, _, _) in PROMPT_SPECS.items()
                                if gr == g.id]}
                   for g in PROMPT_GROUPS],
        "prompts": prompts,
        "tools": tools,
    }
