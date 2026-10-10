"""Admin surface: `GET /v1/admin/health` + runtime-config CRUD (Option A).

- health: static registry health, degraded-never-500 (unchanged contract).
- config: DB-backed runtime variables with in-memory write-through
  (see app/core/runtime.py). GET serves the settings singleton (fast, no
  per-key DB parse); PUT validates all-then-writes (DB first, memory after).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("api.admin")

router = APIRouter()


@router.get("/v1/admin/health")
def admin_health():
    body: dict = {"status": "ok"}
    try:
        from app.api import deps
        from app.core.config import settings
        from app.core.runtime import _pending_keys

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
        try:
            body["disabled_tools"] = sorted(
                t["tool_id"] for t in tools.manifest() if not t.get("enabled", True)
            )
        except Exception:  # noqa: BLE001
            body["disabled_tools"] = []
        try:
            body["pending_restart"] = _pending_keys()
        except Exception:  # noqa: BLE001
            body["pending_restart"] = []
    except Exception as e:  # noqa: BLE001 - degraded, not dead
        logger.warning("admin health degraded: %s", e)
        body = {"status": "degraded", "error": str(e)}
    return body


class ConfigUpdate(BaseModel):
    updates: dict


class ConfigReset(BaseModel):
    keys: list[str] | None = None


def _snapshot_or_503():
    from fastapi import HTTPException

    from app.core import runtime as rt

    try:
        return rt.snapshot()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"config store unavailable: {e}")


@router.get("/v1/admin/config")
def get_config():
    return _snapshot_or_503()


@router.get("/v1/admin/models")
def list_models():
    """Live Ollama model list for the admin dropdown. Degraded-honest."""
    from app.api import deps

    try:
        provider = deps.get_model_provider()
        inner = getattr(provider, "_inner", provider)
        try:
            inner.ensure_ready()
            reachable = True
        except Exception:  # noqa: BLE001 - unreachable Ollama is a state, not an error
            reachable = False
        try:
            models = provider.list_available_models()
        except Exception:  # noqa: BLE001 - listing must never 500 the admin page
            models = []
        return {"reachable": reachable, "models": models}
    except Exception as e:  # noqa: BLE001 - degraded, not dead
        logger.warning("admin models degraded: %s", e)
        return {"reachable": False, "models": []}


@router.put("/v1/admin/config")
def put_config(data: ConfigUpdate):
    from fastapi import HTTPException

    from app.core import runtime as rt

    try:
        applied = rt.apply_updates(data.updates or {})
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    snap = _snapshot_or_503()
    snap["applied"] = applied
    return snap


@router.post("/v1/admin/config/reset")
def reset_config(data: ConfigReset | None = None):
    from fastapi import HTTPException

    from app.core import runtime as rt

    try:
        restored = rt.reset_keys(data.keys if data else None)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    snap = _snapshot_or_503()
    snap["restored"] = restored
    return snap
