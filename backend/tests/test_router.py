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
            "confidence": 0.9,
        }
        self.fail = fail
        self.calls = 0
        self.messages: list = []
        self.timeouts: list[int | None] = []

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None, cancel_event=None):
        raise NotImplementedError("router uses generate_structured")

    def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None):
        self.calls += 1
        self.messages = messages
        self.timeouts.append(timeout_ms)
        if self.fail:
            raise ValueError("ollama down")
        return dict(self.payload)

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake"}]


def test_router_uses_tight_deadline() -> None:
    # Trace 5f98fe9c: the router inherited the full 300s generation budget,
    # so a saturated Ollama held the run hostage (2 x 300s consumed the whole
    # 600s run timeout doing zero work). It must bound its own call.
    from app.core.config import settings

    provider = FakeRouterProvider()
    Router(provider).route("modernize the provided code")
    assert provider.timeouts == [settings.router_timeout_ms]
    assert settings.router_timeout_ms < settings.ollama_timeout_ms


def test_router_timeout_fails_open_fast() -> None:
    # A deadline breach must fail open to UNKNOWN (→ L3 ReAct), not raise.
    from app.orchestration.intents import ROUTER_CONFIDENCE_THRESHOLD

    provider = FakeRouterProvider(fail=True)
    result = Router(provider).route("modernize the provided code")
    assert result.intent is Intent.UNKNOWN
    assert result.confidence < ROUTER_CONFIDENCE_THRESHOLD


def test_greeting_goes_through_llm() -> None:
    # L0 removed: every request — including greetings — is classified by
    # the L1 LLM. The canned payload here returns compare_multi, proving
    # a router call was spent even on short input.
    provider = FakeRouterProvider()
    result = Router(provider).route("hello")
    assert provider.calls == 1
    assert result.routed_by == "llm"
    assert result.intent is Intent.COMPARE_MULTI


def test_chat_intent_maps() -> None:
    provider = FakeRouterProvider(
        {"intent": "chat", "confidence": 0.95}
    )
    result = Router(provider).route("hello there friend, how are you doing?")
    assert result.intent is Intent.CHAT


def test_llm_classification_maps() -> None:
    router = Router(FakeRouterProvider())
    result = router.route("compare both reports and rank them")
    assert result.intent is Intent.COMPARE_MULTI
    assert result.confidence == 0.9


def test_low_confidence_falls_to_unknown() -> None:
    provider = FakeRouterProvider(
        {"intent": "qa_single", "confidence": 0.2}
    )
    result = Router(provider).route("something vague here with length over limit x")
    assert result.intent is Intent.UNKNOWN


def test_bad_intent_string_falls_to_unknown() -> None:
    provider = FakeRouterProvider(
        {"intent": "not_a_real_intent", "confidence": 0.95}
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
    # NB: no "distinct" assert — that word belonged to the removed
    # router-side query-generation line (e2fa13f moved decomposition into
    # rag.query per ADR-030); precedence is covered by the asserts above.


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


def test_convert_slots_parsed() -> None:
    provider = FakeRouterProvider(
        {"intent": "convert_one", "confidence": 0.9,
         "file_hint": "Faultbook", "target_format": "MD"}
    )
    result = Router(provider).route("convert the faultbook report to MD please!")
    assert result.intent is Intent.CONVERT_ONE
    assert result.file_hint == "Faultbook"
    assert result.target_format == "md"  # normalized


def test_convert_slots_default_empty_and_bad_format_dropped() -> None:
    provider = FakeRouterProvider(
        {"intent": "convert_all", "confidence": 0.9,
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


def test_doc_intent_empty_queries_postfilled() -> None:
    # Router no longer returns queries; query generation moves to rag.query tool.
    provider = FakeRouterProvider(
        {"intent": "qa_single", "confidence": 0.95}
    )
    result = Router(provider).route("What is QLoRA?")
    assert result.intent is Intent.QA_SINGLE


def test_non_doc_intent_empty_queries_stay_empty() -> None:
    provider = FakeRouterProvider(
        {"intent": "chat", "confidence": 0.95}
    )
    result = Router(provider).route("hello there friend, how are you doing?")
    assert result.intent is Intent.CHAT


def test_router_prompt_requires_queries_for_doc_intents() -> None:
    provider = FakeRouterProvider()
    Router(provider).route("compare both reports and rank them")
    system = provider.messages[0]["content"]
    # Router prompt no longer mentions queries; query generation moved to rag tool
    assert "queries" not in system.lower() or "never" not in system


def test_router_context_passed_for_followup() -> None:
    # Trace 35e8fbd9: the bare follow-up "in a table format" classified
    # unknown (0.85) because the router never saw the conversation. With
    # context, recent turns reach the router so it can classify the
    # combined intent.
    provider = FakeRouterProvider({"intent": "chat", "confidence": 0.9})
    result = Router(provider).route(
        "in a table format",
        context=(
            "Recent conversation:\n"
            "user: Difference between ONNX and TensorRT\n"
            "assistant: ONNX is a format, TensorRT is an engine."
        ),
    )
    assert result.intent is Intent.CHAT
    context_msg = provider.messages[1]
    assert context_msg["role"] == "system"
    assert "Difference between ONNX and TensorRT" in context_msg["content"]
    assert "follow-up" in context_msg["content"]


def test_router_without_context_keeps_two_messages() -> None:
    provider = FakeRouterProvider({"intent": "chat", "confidence": 0.9})
    Router(provider).route("hello")
    assert len(provider.messages) == 2  # system + user, no context turn
