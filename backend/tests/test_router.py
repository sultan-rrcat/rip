"""Phase 1 — router tests (no Ollama, no DB)."""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.orchestration.intents import Intent
from app.orchestration.router import Router
from app.providers.base import ModelProvider


class FakeRouterProvider(ModelProvider):
    def __init__(self, payload: dict | None = None, fail: bool = False):
        self.payload = payload or {
            "intent": "compare_multi",
            "queries": ["fire report", "faultbook report"],
            "confidence": 0.9,
        }
        self.fail = fail
        self.calls = 0

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None):
        raise NotImplementedError("router uses generate_structured")

    def generate_structured(self, model, messages, schema, *, temperature=0.0):
        self.calls += 1
        if self.fail:
            raise ValueError("ollama down")
        return dict(self.payload)

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake"}]


def test_fast_path_skips_llm() -> None:
    router = Router(FakeRouterProvider())
    result = router.route("hello")
    assert result.intent is Intent.CHAT
    assert result.routed_by == "fast_path"
    assert router._provider.calls == 0


def test_llm_classification_maps() -> None:
    router = Router(FakeRouterProvider())
    result = router.route("compare both reports and rank them")
    assert result.intent is Intent.COMPARE_MULTI
    assert result.queries == ["fire report", "faultbook report"]
    assert result.confidence == 0.9


def test_low_confidence_falls_to_unknown() -> None:
    provider = FakeRouterProvider(
        {"intent": "qa_single", "queries": ["x"], "confidence": 0.2}
    )
    result = Router(provider).route("something vague here with length over limit x")
    assert result.intent is Intent.UNKNOWN


def test_bad_intent_string_falls_to_unknown() -> None:
    provider = FakeRouterProvider(
        {"intent": "not_a_real_intent", "queries": [], "confidence": 0.95}
    )
    result = Router(provider).route("a long enough request that needs the llm path")
    assert result.intent is Intent.UNKNOWN


def test_llm_failure_falls_open_to_unknown() -> None:
    result = Router(FakeRouterProvider(fail=True)).route(
        "a long enough request that needs the llm path"
    )
    assert result.intent is Intent.UNKNOWN
