"""Admin stub (Q37): `GET /v1/admin/health` ONLY.

Static registry health — agent/tool ids plus the configured model. No
PluginManager, no `/plugins`, no `/reload`, no admin console. Anything that
fails to build is reported degraded, never 500: this endpoint must stay up
precisely when other things are down.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger("api.admin")

router = APIRouter()


@router.get("/v1/admin/health")
def admin_health():
    body: dict = {"status": "ok"}
    try:
        from app.api import deps
        from app.core.config import settings

        deps.get_model_provider()
        agents = deps.get_agent_registry()
        tools = deps.get_tool_registry()
        body["model"] = settings.ollama_default_model
        try:
            body["agents"] = sorted(a["agent_id"] for a in agents.manifest())
        except Exception:  # noqa: BLE001 - stub reports, never 500s
            body["agents"] = []
        try:
            body["tools"] = sorted(t["tool_id"] for t in tools.manifest())
        except Exception:  # noqa: BLE001 - stub reports, never 500s
            body["tools"] = []
    except Exception as e:  # noqa: BLE001 - degraded, not dead
        logger.warning("admin health degraded: %s", e)
        body = {"status": "degraded", "error": str(e)}
    return body
