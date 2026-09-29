"""Phase 0 — intent taxonomy tests (no Ollama, no DB)."""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.orchestration.intents import (
    DETERMINISTIC_INTENTS,
    INTENT_DESCRIPTIONS,
    ROUTER_CONFIDENCE_THRESHOLD,
    Intent,
)


def test_all_intents_have_descriptions() -> None:
    for intent in Intent:
        assert INTENT_DESCRIPTIONS[intent].strip(), intent


def test_threshold_in_range() -> None:
    assert 0.0 < ROUTER_CONFIDENCE_THRESHOLD < 1.0


def test_compare_multi_is_deterministic() -> None:
    assert Intent.COMPARE_MULTI in DETERMINISTIC_INTENTS
    assert Intent.UNKNOWN not in DETERMINISTIC_INTENTS
