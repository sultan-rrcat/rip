"""Prompt store + tool-config (DB seeds, live reads, kill-switches).

Unit section runs without Postgres (in-memory cache + code seeds).
API section uses the `client` fixture (skips when Postgres is down).
"""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from app.core import promptstore as ps


def _apply_schema(client):
    # runtime_prompts/tool_config ride schema.sql on fresh bootstrap; ensure
    # them here for volumes created before the tables existed.
    from app.core.db import pg_connection

    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS public.runtime_prompts
               (key text PRIMARY KEY, body text NOT NULL,
                updated_by text, updated_at timestamptz NOT NULL DEFAULT NOW())"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS public.tool_config
               (tool_id text PRIMARY KEY, enabled boolean NOT NULL DEFAULT TRUE,
                description_override text,
                updated_by text, updated_at timestamptz NOT NULL DEFAULT NOW())"""
        )


# ─── Unit: seeds + validation (no DB) ─────────────────────────────────────────


def test_seed_keys_complete():
    seeds = ps._seed_map()
    assert set(seeds) == set(ps.PROMPT_SPECS)
    for key, body in seeds.items():
        assert isinstance(body, str) and body.strip(), key


def test_seed_templates_carry_required_placeholders():
    seeds = ps._seed_map()
    for key, slots in ps.REQUIRED_PLACEHOLDERS.items():
        for slot in slots:
            assert slot in seeds[key], f"{key} seed missing {slot}"


def test_check_body_refuses_missing_placeholder():
    with pytest.raises(ValueError, match="intents_block"):
        ps._check_body("router.system_prompt", "no placeholders here")


def test_check_body_refuses_empty_and_oversize():
    with pytest.raises(ValueError):
        ps._check_body("rag.filter_prompt", "   ")
    with pytest.raises(ValueError, match="exceeds"):
        ps._check_body("agent.coding.description", "x" * (ps.MAX_DESCRIPTION_LEN + 1))


def test_check_body_unknown_key_lookup():
    with pytest.raises(LookupError):
        ps.set_prompt("nope.key", "body")


def test_get_prompt_falls_back_to_seed():
    ps._cache_invalidate_prompt("rag.filter_prompt")
    assert ps.get_prompt("rag.filter_prompt") == ps._seed_map()["rag.filter_prompt"]


def test_agent_voice_and_description_live():
    from app.agents.coding import CodingAgent
    from app.agents.reasoning import ReasoningAgent

    # NOTE: class-level access returns the property object itself (that is
    # what keeps Agent.__init_subclass__ green); live values read via instances.
    assert isinstance(ReasoningAgent(None).description, str)  # type: ignore[arg-type]
    assert ReasoningAgent(None).description.strip()  # type: ignore[arg-type]
    # Live read: a cached override is served without DB.
    ps._cache_set_prompt("agent.reasoning.system_prompt", "LIVE-VOICE")
    try:
        assert ReasoningAgent(None).system_prompt == "LIVE-VOICE"  # type: ignore[arg-type]
    finally:
        ps._cache_invalidate_prompt("agent.reasoning.system_prompt")
    assert ReasoningAgent(None).system_prompt == ps._seed_map()[  # type: ignore[arg-type]
        "agent.reasoning.system_prompt"]
    assert CodingAgent(None).description == ps._seed_map()[  # type: ignore[arg-type]
        "agent.coding.description"]


def test_router_and_react_builders_use_store():
    from app.orchestration.react_loop import _build_react_system_prompt
    from app.orchestration.router import _build_system_prompt

    ps._cache_set_prompt("router.corpus_suffix", "CTX:{corpus_hint}!")
    try:
        out = _build_system_prompt("HINT")
        assert "CTX:HINT!" in out
    finally:
        ps._cache_invalidate_prompt("router.corpus_suffix")
    out = _build_system_prompt()
    assert "{intents_block}" not in out and "{corpus_section}" not in out
    react = _build_react_system_prompt(["coding"], ["rag.query"], None)
    assert "rag.query" in react and "(no documents)" in react


def test_manifest_carries_enabled_and_override():
    from app.tools.registry import get_default_tool_registry

    manifest = {m["tool_id"]: m for m in get_default_tool_registry().manifest()}
    assert set(manifest) == set(ps.TOOL_IDS)
    assert all(m["enabled"] is True for m in manifest.values())
    ps._cache_set_tool("plot.chart", {"enabled": False,
                                      "description_override": "OVERRIDE-DESC"})
    try:
        manifest = {m["tool_id"]: m for m in get_default_tool_registry().manifest()}
        assert manifest["plot.chart"]["enabled"] is False
        assert manifest["plot.chart"]["description"] == "OVERRIDE-DESC"
        assert "plot.chart" not in get_default_tool_registry().enabled_ids()
    finally:
        ps._cache_set_tool("plot.chart", {"enabled": True,
                                          "description_override": None})


def test_validator_rejects_disabled_tool():
    from app.agents.reasoning import ReasoningAgent
    from app.agents.registry import AgentRegistry
    from app.orchestration.plan import Plan, PlanStep
    from app.orchestration.validator import PlanValidationError, PlanValidator
    from app.providers.base import ModelProvider
    from app.tools.registry import get_default_tool_registry

    class _P(ModelProvider):
        def generate(self, *a, **k): raise AssertionError
        def generate_stream(self, *a, **k): raise AssertionError
        def generate_structured(self, *a, **k): raise AssertionError
        def embed(self, *a, **k): raise AssertionError
        def list_available_models(self): return []
        def served_model(self, m): return m

    agents = AgentRegistry()
    agents.register(ReasoningAgent(_P()))
    tools = get_default_tool_registry()
    plan = Plan(plan_id="p", goal="g", steps=[
        PlanStep(step_id="1", tool_id="rag.query",
                 input={"notebook_id": "n", "query": "q"},
                 expected_output_type="chunks"),
    ])
    PlanValidator(agents, tools).validate(plan)  # enabled: passes
    ps._cache_set_tool("rag.query", {"enabled": False,
                                     "description_override": None})
    try:
        with pytest.raises(PlanValidationError, match="disabled tool"):
            PlanValidator(agents, tools).validate(plan)
    finally:
        ps._cache_set_tool("rag.query", {"enabled": True,
                                         "description_override": None})


def test_executor_honest_failure_on_disabled_tool():
    from app.tools.executor import execute_tool
    from app.tools.registry import get_default_tool_registry

    tools = get_default_tool_registry()
    ps._cache_set_tool("code.read", {"enabled": False,
                                     "description_override": None})
    try:
        resp = execute_tool(tools, "code.read", {"notebook_id": "n"},
                            step_id="1", trace_id="t")
        assert resp.ok is False
        assert "disabled" in (resp.error or "")
    finally:
        ps._cache_set_tool("code.read", {"enabled": True,
                                         "description_override": None})


def test_set_tool_enabled_rejects_unknown():
    with pytest.raises(LookupError):
        ps.set_tool_enabled("nope.tool", False)


# ─── API (needs Postgres; skips otherwise) ────────────────────────────────────


def test_prompts_snapshot_shape(client):
    _apply_schema(client)
    r = client.get("/v1/admin/prompts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["prompts"]) == set(ps.PROMPT_SPECS)
    assert set(body["tools"]) == set(ps.TOOL_IDS)
    assert {g["id"] for g in body["groups"]} == {"agents", "router", "react",
                                                "retrieval", "builders"}


def test_prompt_put_reset_roundtrip(client):
    _apply_schema(client)
    r = client.put("/v1/admin/prompts",
                   json={"updates": {"rag.filter_prompt": "Filter now, please."}})
    assert r.status_code == 200, r.text
    assert r.json()["prompts"]["rag.filter_prompt"]["value"] == "Filter now, please."
    assert r.json()["prompts"]["rag.filter_prompt"]["source"] == "db"
    r = client.post("/v1/admin/prompts/reset", json={"keys": ["rag.filter_prompt"]})
    assert r.status_code == 200, r.text
    assert r.json()["prompts"]["rag.filter_prompt"]["source"] == "seed"


def test_prompt_put_refuses_bad_template(client):
    _apply_schema(client)
    r = client.put("/v1/admin/prompts",
                   json={"updates": {"react.system_prompt": "no slots here"}})
    assert r.status_code == 422, r.text
    assert "agent_ids" in r.json()["detail"]


def test_tool_toggle_roundtrip(client):
    _apply_schema(client)
    r = client.put("/v1/admin/tools/plot.chart/enabled", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["tools"]["plot.chart"]["enabled"] is False
    r = client.put("/v1/admin/tools/plot.chart/enabled", json={"enabled": True})
    assert r.json()["tools"]["plot.chart"]["enabled"] is True
    r = client.put("/v1/admin/tools/nope.tool/enabled", json={"enabled": False})
    assert r.status_code == 404


def test_tool_description_roundtrip(client):
    _apply_schema(client)
    r = client.put("/v1/admin/tools/code.read/description",
                   json={"description": "Custom code reader voice."})
    assert r.status_code == 200, r.text
    entry = r.json()["tools"]["code.read"]
    assert entry["description"] == "Custom code reader voice."
    assert entry["description_source"] == "db"
    r = client.post("/v1/admin/tools/reset", json={"tool_ids": ["code.read"]})
    assert r.json()["tools"]["code.read"]["description_source"] == "seed"
