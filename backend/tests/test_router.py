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

    def generate_structured(self, model, messages, schema, *, temperature=0.0, timeout_ms=None, cancel_event=None, max_tokens=None):
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
    provider = FakeRouterProvider(fail=True)
    result = Router(provider).route("modernize the provided code")
    assert result.intent is Intent.UNKNOWN
    assert result.confidence == 0.0


def test_greeting_short_circuits_without_llm() -> None:
    # Cautious pre-filter: pure greetings are deterministic (no LLM call).
    provider = FakeRouterProvider()
    result = Router(provider).route("hello")
    assert provider.calls == 0
    assert result.routed_by == "rule"
    assert result.intent is Intent.CHAT


def test_chat_intent_maps() -> None:
    provider = FakeRouterProvider(
        {"intent": "chat"}
    )
    result = Router(provider).route("hello there friend, how are you doing?")
    assert result.intent is Intent.CHAT


def test_llm_classification_maps() -> None:
    router = Router(FakeRouterProvider())
    result = router.route("compare both reports and rank them")
    assert result.intent is Intent.COMPARE_MULTI
    assert result.confidence == 1.0


def test_no_confidence_threshold_any_parsed_intent_is_trusted() -> None:
    # Confidence was dropped: parse success is trust. A payload that used
    # to fall to UNKNOWN on low confidence now routes to its intent.
    provider = FakeRouterProvider({"intent": "qa_single"})
    result = Router(provider).route("something vague here with length over limit x")
    assert result.intent is Intent.QA_SINGLE
    assert result.confidence == 1.0


def test_bad_intent_string_falls_to_unknown() -> None:
    provider = FakeRouterProvider(
        {"intent": "not_a_real_intent"}
    )
    result = Router(provider).route("a long enough request that needs the llm path")
    assert result.intent is Intent.UNKNOWN


def test_llm_failure_falls_open_to_unknown() -> None:
    result = Router(FakeRouterProvider(fail=True)).route(
        "a long enough request that needs the llm path"
    )
    assert result.intent is Intent.UNKNOWN


def test_prompt_disambiguates_plot_vs_compare() -> None:
    # "compare the class distribution and plot in a bar chart" must carry
    # the precedence rule so a chart ask wins over a compare ask.
    provider = FakeRouterProvider()
    Router(provider).route("compare the class distribution and plot in a bar chart")
    system = provider.messages[0]["content"]
    assert "summarize_plot" in system
    assert "no chart" in system
    assert "Tie-breaks" in system
    assert "stop at the first match" not in system
    assert "c1bbae95" not in system  # no trace anecdotes in the prompt


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


def test_convert_slots_parsed_from_text_not_llm() -> None:
    # Slots are regex-extracted from the request text; LLM payload slots
    # are ignored. Quoted/bare filenames + format word drive the result.
    provider = FakeRouterProvider({"intent": "convert_one"})
    result = Router(provider).route('convert the "faultbook.pdf" report to MD please!')
    assert result.intent is Intent.CONVERT_ONE
    assert result.file_hint == "faultbook.pdf"
    assert result.target_format == "md"  # normalized


def test_convert_slots_default_empty_and_bad_format_dropped() -> None:
    provider = FakeRouterProvider({"intent": "convert_all"})
    result = Router(provider).route("convert all documents to exe somehow here")
    # Slot-guard: no valid md/docx/pdf target → UNKNOWN (ReAct clarifies)
    # instead of a convert label that can only miss in the builder.
    assert result.intent is Intent.UNKNOWN
    assert result.file_hint == "*"
    assert result.target_format == ""  # not a doc.convert format

    legacy = Router(FakeRouterProvider()).route(
        "compare both report and rank them based on complexity"
    )
    assert legacy.file_hint == "" and legacy.target_format == ""


def test_doc_intent_empty_queries_postfilled() -> None:
    # Router no longer returns queries; query generation moves to rag.query tool.
    provider = FakeRouterProvider({"intent": "qa_single"})
    result = Router(provider).route("What is QLoRA?")
    assert result.intent is Intent.QA_SINGLE


def test_non_doc_intent_empty_queries_stay_empty() -> None:
    provider = FakeRouterProvider({"intent": "chat"})
    result = Router(provider).route("hello there friend, how are you doing?")
    assert result.intent is Intent.CHAT


def test_router_prompt_requires_queries_for_doc_intents() -> None:
    provider = FakeRouterProvider()
    Router(provider).route("compare both reports and rank them")
    system = provider.messages[0]["content"]
    # Router prompt no longer mentions queries; query generation moved to rag tool
    assert "queries" not in system.lower() or "never" not in system


def test_router_context_passed_for_followup() -> None:
    # The bare follow-up "in a table format" is unclassifiable alone;
    # with context, recent turns reach the router so it can classify the
    # combined intent.
    provider = FakeRouterProvider({"intent": "chat"})
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
    provider = FakeRouterProvider({"intent": "chat"})
    # "hello" would hit the rule pre-filter (0 messages); use a longer
    # greeting so the LLM path runs and the message shape can be asserted.
    Router(provider).route("hello there friend, how are you doing today?")
    assert len(provider.messages) == 2  # system + user, no context turn


def test_rule_prefilter_convert_all_names_star() -> None:
    provider = FakeRouterProvider({"intent": "should-not-be-used"})
    result = Router(provider).route("convert all documents to pdf please")
    assert provider.calls == 0
    assert result.routed_by == "rule"
    assert result.intent is Intent.CONVERT_ALL
    assert result.file_hint == "*"
    assert result.target_format == "pdf"


def test_rule_prefilter_plot_standalone_needs_numbers() -> None:
    provider = FakeRouterProvider({"intent": "should-not-be-used"})
    result = Router(provider).route("plot bar chart with Alpha: 10, Beta: 20")
    assert provider.calls == 0
    assert result.intent is Intent.PLOT_STANDALONE

    llm_only = FakeRouterProvider({"intent": "summarize_plot"})
    routed = Router(llm_only).route("plot the class distribution from the docs")
    assert llm_only.calls == 1
    assert routed.intent is Intent.SUMMARIZE_PLOT


def test_rule_prefilter_code_marker_without_llm() -> None:
    provider = FakeRouterProvider({"intent": "should-not-be-used"})
    result = Router(provider).route(
        "create a self-contained HTML page, no CDN, return only the HTML"
    )
    assert provider.calls == 0
    assert result.routed_by == "rule"
    assert result.intent is Intent.CODE


def test_llm_slots_ignored_text_wins() -> None:
    # Even if the LLM echoes slots (legacy fakes), the router uses regex.
    provider = FakeRouterProvider(
        {"intent": "convert_one", "file_hint": "wrong", "target_format": "exe"}
    )
    result = Router(provider).route('convert the "real.pdf" to docx please')
    assert result.file_hint == "real.pdf"
    assert result.target_format == "docx"


def test_write_new_content_in_pdf_is_not_a_rule_convert() -> None:
    # "write an email in pdf format" is NEW content (doc.generate via ReAct),
    # not a re-render of an ORIGINAL upload — the pre-filter must stay out.
    from app.orchestration.router import _rule_pre_filter

    assert _rule_pre_filter("write an email in pdf format for my today's leave") is None


def test_prompt_excludes_generate_from_convert() -> None:
    provider = FakeRouterProvider({"intent": "unknown"})
    Router(provider).route("write an email in pdf format for my today's leave")
    system = provider.messages[0]["content"]
    assert "Write/create/generate/draft NEW content" in system
    assert "is NOT convert" in system


CODE_ONLY_SNAPSHOT = (
    "3 file(s): agents.py [ready:code] id=bd29941a-f89e-4c9a-a8f3-08e38e9a0d9d; "
    "admin.py [ready:code] id=921af3f2-80f2-4da8-9a73-4f699af66033; "
    "approvals.py [ready:code] id=ab101bcd-66b7-461c-95ab-4347daf2f568"
)


def test_code_only_snapshot_hints_code_not_empty() -> None:
    # Trace 2e288df9: three [ready:code] files rendered as
    # "EMPTY — no documents uploaded", so "read all the files" routed to
    # summarize and answered "no documents". Code files exist — they are
    # just not searchable documents.
    from app.orchestration.router import _corpus_hint_for_router

    hint = _corpus_hint_for_router(CODE_ONLY_SNAPSHOT)
    assert hint is not None
    assert "CODE-ONLY" in hint
    assert "EMPTY — no documents uploaded" not in hint
    assert "agents.py" in hint

    assert _corpus_hint_for_router("(no documents)") == (
        "Notebook file state: EMPTY — no documents uploaded yet."
    )
    assert _corpus_hint_for_router(None) is None


def test_router_prompt_names_code_files_for_code_routing() -> None:
    # The file list itself is the software signal: the prompt must carry
    # the .py names plus the rule that reviewing those files IS code.
    provider = FakeRouterProvider({"intent": "code"})
    result = Router(provider).route(
        "Read all the files and identify issues",
        notebook_context=CODE_ONLY_SNAPSHOT,
    )
    assert result.intent is Intent.CODE
    system = provider.messages[0]["content"]
    assert "agents.py" in system
    assert "notebook" in system.lower() and "code" in system.lower()
