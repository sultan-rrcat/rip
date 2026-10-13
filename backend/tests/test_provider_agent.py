"""Unit tests for the shared ProviderAgent base class and _Watchdog.

These tests pin the common agent contract: message extraction, provider
call (streaming or not), empty-output-as-failure, exception wrapping,
and the first-visible-token watchdog. No real dependencies required.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from app.agents.base import DelegationRequest, StepStatus
from app.agents.provider_agent import ProviderAgent, _Watchdog
from app.providers.base import ModelProvider


class ConcreteAgent(ProviderAgent):
    """Minimal concrete agent for testing the base class."""

    agent_id = "concrete"
    name = "Concrete Agent"
    description = "Test agent"
    system_prompt = "test prompt"
    default_budget = 1024

    def __init__(self, provider: ModelProvider):
        super().__init__(provider)


def _make_request(**kwargs) -> DelegationRequest:
    defaults = {
        "step_id": "s1",
        "trace_id": "t1",
        "input": {"message": "hello"},
    }
    defaults.update(kwargs)
    return DelegationRequest(**defaults)


class TestProviderAgentCreation:
    def test_cannot_instantiate_base_directly(self) -> None:
        try:
            ProviderAgent(MagicMock())
            assert False, "Should not be instantiable"
        except TypeError:
            pass

    def test_concrete_agent_instantiates(self) -> None:
        agent = ConcreteAgent(MagicMock())
        assert agent.agent_id is not None

    def test_metadata_enforced_on_subclass(self) -> None:
        try:
            class BadAgent(ProviderAgent):
                system_prompt = "p"
                default_budget = 100

            assert False, "Should not be definable"
        except TypeError as e:
            assert "agent_id" in str(e)


class TestProviderAgentExecute:
    def test_missing_message_returns_failure(self) -> None:
        provider = MagicMock()
        agent = ConcreteAgent(provider)
        request = _make_request(input={})
        result = agent.execute(request)
        assert result.status is StepStatus.FAILURE
        assert "message" in (result.error or "")

    def test_successful_generate(self) -> None:
        provider = MagicMock()
        provider.generate.return_value = "hello world"
        agent = ConcreteAgent(provider)
        result = agent.execute(_make_request())
        assert result.status is StepStatus.SUCCESS
        assert result.output == "hello world"

    def test_empty_output_is_failure(self) -> None:
        provider = MagicMock()
        provider.generate.return_value = ""
        agent = ConcreteAgent(provider)
        result = agent.execute(_make_request())
        assert result.status is StepStatus.FAILURE
        assert "no text" in (result.error or "")

    def test_whitespace_output_is_failure(self) -> None:
        provider = MagicMock()
        provider.generate.return_value = "   "
        agent = ConcreteAgent(provider)
        result = agent.execute(_make_request())
        assert result.status is StepStatus.FAILURE

    def test_exception_wrapped_as_failure(self) -> None:
        provider = MagicMock()
        provider.generate.side_effect = RuntimeError("boom")
        agent = ConcreteAgent(provider)
        result = agent.execute(_make_request())
        assert result.status is StepStatus.FAILURE
        assert "boom" in (result.error or "")

    def test_streaming_generate(self) -> None:
        provider = MagicMock()
        provider.generate_stream.return_value = iter(["a", "b", "c"])
        agent = ConcreteAgent(provider)
        request = _make_request()
        deltas: list[str] = []
        request.on_delta = deltas.append
        result = agent.execute(request)
        assert result.status is StepStatus.SUCCESS
        assert result.output == "abc"
        assert deltas == ["a", "b", "c"]

    def test_history_and_context_included(self) -> None:
        provider = MagicMock()
        provider.generate.return_value = "ok"
        agent = ConcreteAgent(provider)
        request = _make_request(input={
            "message": "hello",
            "history": [{"role": "user", "content": "hi"}],
            "context": "ctx",
        })
        agent.execute(request)
        messages = provider.generate.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "test prompt"}
        assert {"role": "system", "content": "Conversation context:\nctx"} in messages
        assert {"role": "user", "content": "hi"} in messages
        assert messages[-1] == {"role": "user", "content": "hello"}

    def test_budget_uses_request_override(self) -> None:
        provider = MagicMock()
        provider.generate.return_value = "ok"
        agent = ConcreteAgent(provider)
        request = _make_request()
        request.max_tokens = 512
        agent.execute(request)
        assert provider.generate.call_args.kwargs["max_tokens"] == 512

    def test_budget_uses_agent_default(self) -> None:
        provider = MagicMock()
        provider.generate.return_value = "ok"
        agent = ConcreteAgent(provider)
        agent.execute(_make_request())
        assert provider.generate.call_args.kwargs["max_tokens"] == 1024

    def test_cancel_event_forwarded(self) -> None:
        provider = MagicMock()
        provider.generate.return_value = "ok"
        agent = ConcreteAgent(provider)
        request = _make_request()
        request.cancel_event = threading.Event()
        agent.execute(request)
        assert provider.generate.call_args.kwargs["cancel_event"] is request.cancel_event


class TestWatchdog:
    def test_no_trip_when_visible_quickly(self) -> None:
        cancel = threading.Event()
        wd = _Watchdog(timeout_s=60.0, cancel_event=cancel)
        wd.start()
        wd.mark_visible()
        time.sleep(0.1)
        assert not wd.check_trip()
        wd.stop()

    def test_trips_on_timeout(self) -> None:
        cancel = threading.Event()
        wd = _Watchdog(timeout_s=0.2, cancel_event=cancel)
        wd.start()
        time.sleep(1.0)
        assert wd.check_trip()
        wd.stop()

    def test_cancel_event_stops_watchdog(self) -> None:
        cancel = threading.Event()
        wd = _Watchdog(timeout_s=60.0, cancel_event=cancel)
        stop = wd.start()
        cancel.set()
        time.sleep(0.1)
        wd.stop()
        assert stop.is_set()

    def test_pure_silence_still_trips_fast(self) -> None:
        # No activity at all: trip at the first window (unchanged behavior).
        cancel = threading.Event()
        wd = _Watchdog(timeout_s=0.2, cancel_event=cancel)
        wd.start()
        time.sleep(1.0)
        assert wd.check_trip()
        wd.stop()

    def test_reasoning_activity_grants_extensions_then_trips(self) -> None:
        # Trace 7cf93519: granite4.2 streams delta.reasoning for a minute+
        # before the first visible token. Activity extends the guard, but
        # the total stays bounded (timeout * (1 + max_extensions)).
        cancel = threading.Event()
        wd = _Watchdog(timeout_s=0.2, cancel_event=cancel, max_extensions=1)
        wd.start()
        for _ in range(6):
            time.sleep(0.15)
            wd.mark_active()
        assert not wd.check_trip()
        deadline = time.monotonic() + 8.0
        while not wd.check_trip() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert wd.check_trip()
        wd.stop()

    def test_no_extensions_by_default_count(self) -> None:
        cancel = threading.Event()
        wd = _Watchdog(timeout_s=0.2, cancel_event=cancel, max_extensions=0)
        wd.start()
        wd.mark_active()
        time.sleep(1.0)
        assert wd.check_trip()
        wd.stop()


class TestWatchdogReasoningActivity:
    def test_reasoning_then_visible_succeeds(self) -> None:
        """Trace 7cf93519: reasoning-only stream activity must hold the
        guard until visible tokens arrive."""

        class GuardedAgent(ConcreteAgent):
            agent_id = "guarded"

            def _start_watchdog(self, request):
                return _Watchdog(
                    timeout_s=0.3, cancel_event=request.cancel_event,
                    max_extensions=5,
                )

        seen: list[str] = []

        class ReasoningProvider(ModelProvider):
            def generate(self, *a, **k):
                raise AssertionError

            def generate_stream(self, model, messages, **kwargs):
                on_activity = kwargs.get("on_activity")
                for _ in range(5):
                    time.sleep(0.2)
                    if on_activity is not None:
                        on_activity()
                seen.append("signaled")
                yield "done"

            def generate_structured(self, *a, **k):
                raise AssertionError

            def embed(self, *a, **k):
                raise AssertionError

            def list_available_models(self):
                return []

            def served_model(self, m):
                return m

        agent = GuardedAgent(ReasoningProvider())
        request = _make_request(on_delta=lambda c: None)
        result = agent.execute(request)
        assert seen == ["signaled"]
        assert result.status is StepStatus.SUCCESS
        assert result.output == "done"

    def test_silent_stream_still_trips(self) -> None:
        """No activity at all: the guard still fails the step fast."""

        class GuardedAgent(ConcreteAgent):
            agent_id = "guarded-silent"

            def _start_watchdog(self, request):
                return _Watchdog(
                    timeout_s=0.3, cancel_event=request.cancel_event,
                    max_extensions=5,
                )

        class SilentProvider(ModelProvider):
            def generate(self, *a, **k):
                raise AssertionError

            def generate_stream(self, model, messages, **kwargs):
                cancel = kwargs.get("cancel_event")
                for _ in range(30):
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("run cancelled")
                    time.sleep(0.1)
                yield "too late"

            def generate_structured(self, *a, **k):
                raise AssertionError

            def embed(self, *a, **k):
                raise AssertionError

            def list_available_models(self):
                return []

            def served_model(self, m):
                return m

        agent = GuardedAgent(SilentProvider())
        request = _make_request(on_delta=lambda c: None)
        result = agent.execute(request)
        assert result.status is StepStatus.FAILURE
        assert "no visible output" in (result.error or "")

    def test_visible_stream_is_not_cancelled(self) -> None:
        """Regression (trace 9c73044b): _stop doubles as the provider's
        cancel_event, so setting it on first-visible-token aborts the
        healthy stream as 'run cancelled'. Visible chunks must flow."""

        class GuardedAgent(ConcreteAgent):
            agent_id = "guarded-healthy"

            def _start_watchdog(self, request):
                return _Watchdog(
                    timeout_s=60.0, cancel_event=request.cancel_event,
                    max_extensions=5,
                )

        class SlowVisibleProvider(ModelProvider):
            def generate(self, *a, **k):
                raise AssertionError

            def generate_stream(self, model, messages, **kwargs):
                cancel = kwargs.get("cancel_event")
                for piece in ("one ", "two ", "three"):
                    for _ in range(12):
                        if cancel is not None and cancel.is_set():
                            raise RuntimeError("run cancelled")
                        time.sleep(0.1)
                    yield piece

            def generate_structured(self, *a, **k):
                raise AssertionError

            def embed(self, *a, **k):
                raise AssertionError

            def list_available_models(self):
                return []

            def served_model(self, m):
                return m

        agent = GuardedAgent(SlowVisibleProvider())
        request = _make_request(on_delta=lambda c: None)
        result = agent.execute(request)
        assert result.status is StepStatus.SUCCESS
        assert result.output == "one two three"