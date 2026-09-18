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
        self.messages: list = []

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None):
        raise NotImplementedError("router uses generate_structured")

    def generate_structured(self, model, messages, schema, *, temperature=0.0):
        self.calls += 1
        self.messages = messages
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


def test_prompt_disambiguates_plot_vs_compare() -> None:
    # Live trace: "compare the class distribution and plot in a bar chart"
    # was routed compare_multi (no plot step emitted). The prompt must carry
    # the precedence rule so a chart ask wins over a compare ask.
    provider = FakeRouterProvider()
    Router(provider).route("compare the class distribution and plot in a bar chart")
    system = provider.messages[0]["content"]
    assert "summarize_plot" in system
    assert "compare_multi is only for comparisons with no chart" in system
    assert "distinct" in system


def test_prompt_built_from_descriptions_with_precedence() -> None:
    # The descriptions themselves must separate the two intents: compare
    # excludes charts, summarize_plot claims them even alongside compare.
    from app.orchestration.intents import INTENT_DESCRIPTIONS

    assert "no chart" in INTENT_DESCRIPTIONS[Intent.COMPARE_MULTI]
    assert "even when the request also says compare" in INTENT_DESCRIPTIONS[
        Intent.SUMMARIZE_PLOT
    ]


def test_compare_without_chart_stays_compare_multi() -> None:
    # Trace 2's request has no chart ask: the canned compare_multi payload
    # must pass through untouched (precedence rule must not steal it).
    result = Router(FakeRouterProvider()).route(
        "compare both report and rank them based on complexity"
    )
    assert result.intent is Intent.COMPARE_MULTI
    assert len(result.queries) == 2


def test_convert_slots_parsed() -> None:
    provider = FakeRouterProvider(
        {"intent": "convert_one", "queries": [], "confidence": 0.9,
         "file_hint": "Faultbook", "target_format": "MD"}
    )
    result = Router(provider).route("convert the faultbook report to MD please!")
    assert result.intent is Intent.CONVERT_ONE
    assert result.file_hint == "Faultbook"
    assert result.target_format == "md"  # normalized


def test_convert_slots_default_empty_and_bad_format_dropped() -> None:
    provider = FakeRouterProvider(
        {"intent": "convert_all", "queries": [], "confidence": 0.9,
         "file_hint": "*", "target_format": "exe"}
    )
    result = Router(provider).route("convert all documents to exe somehow here")
    assert result.intent is Intent.CONVERT_ALL
    assert result.file_hint == "*"
    assert result.target_format == ""  # not a doc.convert format

    legacy = Router(FakeRouterProvider()).route(
        "compare both report and rank them based on complexity"
    )
    assert legacy.file_hint == "" and legacy.target_format == ""
