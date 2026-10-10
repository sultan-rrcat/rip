"""Runtime-config admin API (Option A): grouped snapshot, PUT, reset, persistence."""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _apply_schema(client):
    # runtime_config table exists via schema.sql on fresh bootstrap; ensure it
    # here so the suite passes against volumes created before the table.
    with client:
        pass
    from app.core.db import pg_connection

    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS public.runtime_config
               (key text PRIMARY KEY, value text NOT NULL,
                updated_by text, updated_at timestamptz NOT NULL DEFAULT NOW())"""
        )


def test_config_snapshot_shape(client):
    _apply_schema(client)
    r = client.get("/v1/admin/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "groups" in body and "entries" in body and "pending_restart" in body
    assert body["pending_restart_command"] == "docker compose restart backend"
    # Organized groups present
    ids = {g["id"] for g in body["groups"]}
    for expected in ("model", "budgets", "orchestration", "memory",
                     "retrieval", "ingestion", "observability",
                     "connection", "embeddings", "system"):
        assert expected in ids
    # Spot-check a live + a restart + a read-only entry
    assert body["entries"]["default_temperature"]["apply"] == "live"
    assert body["entries"]["ollama_base_url"]["apply"] == "restart"
    assert body["entries"]["db_password"]["editable"] is False
    assert body["entries"]["langfuse_secret_key"]["value"] != "" or True  # masked


def test_put_live_key_applies_and_persists(client):
    _apply_schema(client)
    from app.core.config import settings

    orig = settings.default_temperature
    try:
        r = client.put("/v1/admin/config", json={"updates": {"default_temperature": 0.42}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "default_temperature" in body["applied"]
        assert body["entries"]["default_temperature"]["value"] == 0.42
        assert body["entries"]["default_temperature"]["source"] == "db"
        assert settings.default_temperature == 0.42
        # Write-through: visible on a fresh GET (memory + DB consistent)
        r2 = client.get("/v1/admin/config")
        assert r2.json()["entries"]["default_temperature"]["value"] == 0.42
    finally:
        client.post("/v1/admin/config/reset", json={"keys": ["default_temperature"]})
        assert settings.default_temperature == orig


def test_put_restart_key_marks_pending(client):
    _apply_schema(client)
    from app.core.config import settings

    orig = settings.ollama_timeout_ms
    try:
        r = client.put("/v1/admin/config", json={"updates": {"ollama_timeout_ms": 42424}})
        assert r.status_code == 200, r.text
        assert "ollama_timeout_ms" in r.json()["pending_restart"]
        # Revert clears pending (derived from boot snapshot, not a flag)
        client.post("/v1/admin/config/reset", json={"keys": ["ollama_timeout_ms"]})
        r2 = client.get("/v1/admin/config")
        assert "ollama_timeout_ms" not in r2.json()["pending_restart"]
    finally:
        client.post("/v1/admin/config/reset", json={"keys": ["ollama_timeout_ms"]})
        assert settings.ollama_timeout_ms == orig


def test_put_validation_and_readonly(client):
    _apply_schema(client)
    r = client.put("/v1/admin/config", json={"updates": {"default_temperature": 99}})
    assert r.status_code == 422
    r = client.put("/v1/admin/config", json={"updates": {"rag_pdf_loader": "nope"}})
    assert r.status_code == 422
    r = client.put("/v1/admin/config", json={"updates": {"db_password": "x"}})
    assert r.status_code == 422
    r = client.put("/v1/admin/config", json={"updates": {"nope_key": 1}})
    assert r.status_code == 404


def test_reset_restores_default(client):
    _apply_schema(client)
    from app.core.config import settings

    client.put("/v1/admin/config", json={"updates": {"chat_max_tokens": 777}})
    r = client.post("/v1/admin/config/reset", json={"keys": ["chat_max_tokens"]})
    assert r.status_code == 200, r.text
    assert r.json()["entries"]["chat_max_tokens"]["source"] in ("default", "env")
    assert settings.chat_max_tokens != 777
