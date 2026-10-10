"""Admin surface: prompts + tool-config CRUD (write-through, see promptstore).

- GET /v1/admin/prompts: grouped prompt entries + per-tool config table.
- PUT /v1/admin/prompts {updates}: validate ALL first (422 names the bad
  key — including missing {placeholders} on templated prompts), then write
  DB-first/memory-after. Never half-applies.
- POST /v1/admin/prompts/reset {keys?}: delete overrides → code seeds.
- PUT /v1/admin/tools/{id}/enabled {enabled}: kill-switch (no last-enabled
  guard by design — the frontend confirms explicitly).
- PUT /v1/admin/tools/{id}/description {description}: menu-text override.
- POST /v1/admin/tools/reset {tool_ids?}: back to seeds (enabled + no
  override). Reads serve memory (no per-request DB latency); the Redis move
  later swaps the promptstore backend without touching these routes.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("api.prompt_admin")

router = APIRouter()


class PromptUpdate(BaseModel):
    updates: dict


class PromptReset(BaseModel):
    keys: list[str] | None = None


class ToolToggle(BaseModel):
    enabled: bool


class ToolDescription(BaseModel):
    description: str


class ToolReset(BaseModel):
    tool_ids: list[str] | None = None


def _snapshot_or_503():
    from fastapi import HTTPException

    from app.core import promptstore as ps

    try:
        return ps.snapshot()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"prompt store unavailable: {e}")


@router.get("/v1/admin/prompts")
def get_prompts():
    return _snapshot_or_503()


@router.put("/v1/admin/prompts")
def put_prompts(data: PromptUpdate):
    from fastapi import HTTPException

    from app.core import promptstore as ps

    updates = data.updates or {}
    if not updates:
        raise HTTPException(status_code=422, detail="updates must not be empty")
    # Validate ALL before writing ANY (validate-then-write, no half-apply).
    try:
        for key, body in updates.items():
            if key not in ps.PROMPT_SPECS:
                raise LookupError(f"unknown prompt key: {key}")
            ps._check_body(key, body)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        applied = [ps.set_prompt(k, v) for k, v in updates.items()]
    except (LookupError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    snap = _snapshot_or_503()
    snap["applied"] = applied
    return snap


@router.post("/v1/admin/prompts/reset")
def reset_prompts(data: PromptReset | None = None):
    from fastapi import HTTPException

    from app.core import promptstore as ps

    try:
        restored = ps.reset_prompts(data.keys if data else None)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    snap = _snapshot_or_503()
    snap["restored"] = restored
    return snap


@router.put("/v1/admin/tools/{tool_id}/enabled")
def toggle_tool(tool_id: str, data: ToolToggle):
    from fastapi import HTTPException

    from app.core import promptstore as ps

    try:
        enabled = ps.set_tool_enabled(tool_id, data.enabled)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    snap = _snapshot_or_503()
    snap["toggled"] = {tool_id: enabled}
    return snap


@router.put("/v1/admin/tools/{tool_id}/description")
def set_tool_description(tool_id: str, data: ToolDescription):
    from fastapi import HTTPException

    from app.core import promptstore as ps

    try:
        ps.set_tool_description(tool_id, data.description)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return _snapshot_or_503()


@router.post("/v1/admin/tools/reset")
def reset_tools(data: ToolReset | None = None):
    from fastapi import HTTPException

    from app.core import promptstore as ps

    try:
        restored = ps.reset_tools(data.tool_ids if data else None)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    snap = _snapshot_or_503()
    snap["restored"] = restored
    return snap
