"""Cancellation propagation: the run's stop flag must reach the LLM calls.

Regression tests for the stop-button hang: `cancel_event` was polled at
step/iteration boundaries but never reached the blocking Ollama HTTP calls,
so an in-flight generation ran to the 300s HTTP timeout before the run
could unwind to `cancelled`. These tests pin the wiring — every provider
call made on behalf of a run carries the run's cancel_event.

Trace d9b9a296 extension: a step wall-clock timeout must also abort the
in-flight generation — no generation may outlive its step's deadline.
"""
from __future__ import annotations

import contextvars
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
                            timeout_ms=None, cancel_event=None, max_tokens=None):
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
        # CodingAgent ORs run-cancel with its quiet-stream guard, so the
        # provider sees a derived watchdog event, not the run event
        # itself — but exactly one must be forwarded, never None.
        assert len(provider.seen) == 1
        assert provider.seen[0] is not None

    def test_coding_aborts_when_run_cancelled(self):
        from app.agents.base import StepStatus
        from app.agents.coding import CodingAgent

        class _HonoringProvider(_RecordingProvider):
            def generate(self, model, messages, *, temperature=0.2,
                         max_tokens=None, cancel_event=None):
                self.seen.append(cancel_event)
                for _ in range(100):
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("run cancelled")
                    time.sleep(0.02)
                return "ok"

        provider = _HonoringProvider()
        event = _event()
        event.set()
        resp = CodingAgent(provider).execute(
            DelegationRequest(
                step_id="1", trace_id="t", input={"message": "write fizzbuzz"},
                cancel_event=event,
            )
        )
        assert resp.status is StepStatus.FAILURE
        assert "run cancelled" in (resp.error or "")

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


class TestStepTimeoutAbortsGeneration:
    """Trace d9b9a296: step timeout must cancel the in-flight generation."""

    def test_no_generation_outlives_step_deadline(self):
        from app.agents.coding import CodingAgent
        from app.agents.registry import AgentRegistry
        from app.orchestration.plan import PlanStep
        from app.orchestration.plan_graph import (
            _invoke_with_wall_clock,
            _run_step_body,
        )

        class _HangingProvider:
            """Blocks until cancelled, then aborts promptly."""

            def __init__(self):
                self.end_time: float | None = None
                self.seen = None

            def _await(self, cancel_event, duration=30.0):
                end = time.monotonic() + duration
                while time.monotonic() < end:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("run cancelled")
                    time.sleep(0.02)

            def generate(self, model, messages, *, temperature=0.2,
                         max_tokens=None, cancel_event=None):
                self.seen = cancel_event
                try:
                    self._await(cancel_event)
                    return "late success (must be discarded)"
                finally:
                    self.end_time = time.monotonic()

            def generate_stream(self, model, messages, *, temperature=0.2,
                                max_tokens=None, cancel_event=None):
                self.seen = cancel_event
                try:
                    self._await(cancel_event)
                    yield "late success (must be discarded)"
                finally:
                    self.end_time = time.monotonic()

            def generate_structured(self, *a, **k):
                raise NotImplementedError("test fake")

            def embed(self, model, text):
                raise NotImplementedError("test fake")

            def list_available_models(self):
                return [{"id": "fake"}]

        provider = _HangingProvider()
        registry = AgentRegistry()
        registry.register(CodingAgent(provider))
        step = PlanStep(
            step_id="1", agent_id="coding", input={"message": "write it"},
            expected_output_type="text",
        )
        expired = threading.Event()
        abort = threading.Event()
        run_cancel = threading.Event()  # never set: only the timeout fires

        def body():
            return _run_step_body(
                step, "t", {"message": "write it"}, registry,
                0, 30_000, run_cancel, None, None, expired, abort,
            )

        start = time.monotonic()
        result = _invoke_with_wall_clock(
            step, contextvars.copy_context(), body,
            timeout_ms=300, cancel_event=run_cancel,
            expired=expired, abort_event=abort,
        )
        wall_elapsed = time.monotonic() - start
        assert result.error == "step timed out"
        assert expired.is_set()
        assert abort.is_set()
        # CodingAgent wraps the step abort in its own watchdog event, so
        # the provider never sees `abort` itself — but the wrapper must
        # have fired as a result of the timeout.
        assert provider.seen is not None
        # The wall clock already fired; the orphaned generation must now
        # finish promptly (bounded wait — a regression hangs the full
        # 30s provider budget instead).
        deadline = time.monotonic() + 5.0
        while provider.end_time is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert provider.end_time is not None, "orphaned generation never aborted"
        assert provider.seen.is_set()
        # Generation must end promptly after the deadline, not run to
        # its 30s HTTP budget.
        assert provider.end_time is not None
        assert provider.end_time - start < 3.0, (
            provider.end_time - start, wall_elapsed
        )

    def test_output_limit_failure_does_not_retry(self):
        from app.agents.base import Agent, DelegationResponse, StepStatus
        from app.agents.registry import AgentRegistry
        from app.orchestration.plan import PlanStep
        from app.orchestration.plan_graph import _run_step_body

        class _TruncatedAgent(Agent):
            agent_id = "truncated"
            name = "Truncated"
            description = "always hits output limit"

            def __init__(self):
                self.calls = 0

            def execute(self, request) -> DelegationResponse:
                self.calls += 1
                return DelegationResponse(
                    step_id=request.step_id,
                    status=StepStatus.FAILURE,
                    error=(
                        "Execution failed: model hit its output limit "
                        "before answering (finish_reason=length, no visible text)"
                    ),
                )

        registry = AgentRegistry()
        agent = _TruncatedAgent()
        registry.register(agent)
        result = _run_step_body(
            PlanStep(step_id="1", agent_id="truncated",
                     input={"message": "do it"},
                     expected_output_type="text"),
            "t", {"message": "do it"}, registry,
            2, 120_000, None, None, None, threading.Event(), threading.Event(),
        )
        assert result.status.value == "failure"
        assert "output limit" in (result.error or "")
        assert agent.calls == 1, agent.calls
