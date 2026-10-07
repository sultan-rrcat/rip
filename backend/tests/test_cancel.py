"""Cancellation propagation: the run's stop flag must reach the LLM calls.

Regression tests for the stop-button hang: `cancel_event` was polled at
step/iteration boundaries but never reached the blocking Ollama HTTP calls,
so an in-flight generation ran to the 300s HTTP timeout before the run
could unwind to `cancelled`. These tests pin the wiring — every provider
call made on behalf of a run carries the run's cancel_event.
"""
from __future__ import annotations

import threading
import time

from app.agents.base import DelegationRequest
from app.orchestration.router import Router
from app.providers.ollama import _await_thread


class _RecordingProvider:
    """Fake provider that records the cancel_event it was given."""

    def __init__(self, structured: dict | None = None):
        self.seen: list = []
        self.structured = structured or {"queries": ["q"]}

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None,
                 cancel_event=None):
        self.seen.append(cancel_event)
        return "ok"

    def generate_stream(self, model, messages, *, temperature=0.2,
                        max_tokens=None, cancel_event=None):
        self.seen.append(cancel_event)
        yield "ok"

    def generate_structured(self, model, messages, schema, *, temperature=0.0,
                            timeout_ms=None, cancel_event=None):
        self.seen.append(cancel_event)
        return dict(self.structured)

    def embed(self, model, text):
        raise NotImplementedError("test fake")

    def list_available_models(self):
        return [{"id": "fake"}]

    def served_model(self, requested_model: str) -> str:
        return requested_model


def _event() -> threading.Event:
    return threading.Event()


class TestCancelForwarding:
    def test_reasoning_streaming_forwards_cancel_event(self):
        from app.agents.reasoning import ReasoningAgent

        provider = _RecordingProvider()
        event = _event()
        resp = ReasoningAgent(provider).execute(
            DelegationRequest(
                step_id="1", trace_id="t", input={"message": "hi"},
                on_delta=lambda _c: None, cancel_event=event,
            )
        )
        assert resp.output == "ok"
        assert provider.seen == [event]

    def test_reasoning_non_streaming_forwards_cancel_event(self):
        from app.agents.reasoning import ReasoningAgent

        provider = _RecordingProvider()
        event = _event()
        resp = ReasoningAgent(provider).execute(
            DelegationRequest(
                step_id="1", trace_id="t", input={"message": "hi"},
                cancel_event=event,
            )
        )
        assert resp.output == "ok"
        assert provider.seen == [event]

    def test_coding_forwards_cancel_event(self):
        from app.agents.coding import CodingAgent

        provider = _RecordingProvider()
        event = _event()
        resp = CodingAgent(provider).execute(
            DelegationRequest(
                step_id="1", trace_id="t", input={"message": "write fizzbuzz"},
                cancel_event=event,
            )
        )
        assert resp.output == "ok"
        assert provider.seen == [event]

    def test_rag_query_planner_forwards_cancel_event(self):
        from app.tools.base import ToolRequest
        from app.tools.rag_query import RagQueryTool

        class _Rag:
            def retrieve_context(self, notebook_id, query, top_k=4,
                                 file_id=None, file_name=None, mode="specific"):
                return {"results": []}

        provider = _RecordingProvider()
        event = _event()
        resp = RagQueryTool(rag=_Rag(), provider=provider).execute(
            ToolRequest(
                tool_id="rag.query", step_id="1", trace_id="t",
                input={"notebook_id": "nb-1", "query": "q"},
                cancel_event=event,
            )
        )
        assert resp.ok
        assert provider.seen == [event]

    def test_router_forwards_cancel_event(self):
        provider = _RecordingProvider(
            {"intent": "chat"}
        )
        event = _event()
        # "hello" hits the rule pre-filter (no LLM call), so use an
        # ambiguous request that must reach the LLM to assert forwarding.
        result = Router(provider).route(
            "hello there friend, how are you doing today?", cancel_event=event
        )
        assert result.intent.value == "chat"
        assert provider.seen == [event]


class TestCancellableBlockingCall:
    def test_await_thread_raises_promptly_on_cancel(self):
        done = threading.Event()

        def _slow() -> None:
            done.wait(timeout=30)

        worker = threading.Thread(target=_slow, daemon=True)
        worker.start()
        event = _event()
        event.set()
        start = time.monotonic()
        try:
            _await_thread(worker, event)
        except RuntimeError as e:
            assert str(e) == "run cancelled"
        else:  # pragma: no cover - must raise
            raise AssertionError("expected RuntimeError")
        assert time.monotonic() - start < 5
        done.set()

    def test_await_thread_waits_when_not_cancelled(self):
        worker = threading.Thread(target=lambda: None)
        worker.start()
        _await_thread(worker, None)  # returns normally

    def test_post_stream_rejects_preset_cancel_without_network(self):
        from app.providers.ollama import OllamaProvider

        real_probe = OllamaProvider._probe_once
        OllamaProvider._probe_once = lambda self, timeout: False
        try:
            provider = OllamaProvider()
        finally:
            OllamaProvider._probe_once = real_probe
        event = _event()
        event.set()
        try:
            list(provider._post_stream({"model": "m"}, cancel_event=event))
        except RuntimeError as e:
            assert str(e) == "run cancelled"
        else:  # pragma: no cover - must raise
            raise AssertionError("expected RuntimeError")

    def test_structured_rejects_preset_cancel_without_network(self):
        from app.providers.ollama import OllamaProvider

        real_probe = OllamaProvider._probe_once
        OllamaProvider._probe_once = lambda self, timeout: False
        try:
            provider = OllamaProvider()
        finally:
            OllamaProvider._probe_once = real_probe
        provider._reachable = True  # skip the re-probe in ensure_ready
        provider._available = set()
        event = _event()
        event.set()
        try:
            provider.generate_structured(
                "m", [{"role": "user", "content": "hi"}], {"type": "object"},
                cancel_event=event,
            )
        except RuntimeError as e:
            assert str(e) == "run cancelled"
        else:  # pragma: no cover - must raise
            raise AssertionError("expected RuntimeError")
