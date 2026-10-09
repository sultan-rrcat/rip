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
from app.core import constants
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
        stop = wd.start()
        wd.mark_visible()
        time.sleep(0.1)
        assert not wd.check_trip()
        wd.stop()

    def test_trips_on_timeout(self) -> None:
        cancel = threading.Event()
        wd = _Watchdog(timeout_s=0.2, cancel_event=cancel)
        stop = wd.start()
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