"""GET /health — liveness probe (copied from Athena, stdlib logging)."""
from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger("api.health")

router = APIRouter()


@router.get("/health")
def health_check():
    logger.debug("health check")
    return {"status": "ok"}
