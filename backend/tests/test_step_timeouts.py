"""Step wall-clock guards (trace b4301187 — ornith coding timeout).

- Coding agent aborts attempts that stream nothing visible (think-only burn)
  instead of riding a full max_tokens budget to an empty result.
- The step retry loop never launches an attempt that cannot plausibly
  finish inside the remaining wall-clock (no orphaned Ollama generations
  whose success lands past the deadline).
No Ollama, no DB.
"""

from __future__ import annotations

import os
import sys
import threading
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.agents.base import (
    Agent,
    DelegationRequest,
    DelegationResponse,
    StepStatus,
)
from app.agents.coding import CodingAgent
from app.agents.registry import AgentRegistry
from app.orchestration.plan import PlanStep
from app.orchestration.plan_graph import _run_step_body
from app.providers.base import ModelProvider


class SilentStreamProvider(ModelProvider):
    """Blocks `silent_for_s` without yielding, then yields `tail`."""

    def __init__(self, silent_for_s: float = 5.0, tail: str = ""):
        self.silent_for_s = silent_for_s
        self.tail = tail

    def _await(self, cancel_event, duration: float) -> None:
        end = time.monotonic() + duration
        while time.monotonic() < end:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("run cancelled")
            time.sleep(0.02)

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None,
                 cancel_event=None):
        self._await(cancel_event, self.silent_for_s)
        return self.tail

    def generate_stream(self, model, messages, *, temperature=0.2,
                        max_tokens=None, cancel_event=None):
        self._await(cancel_event, self.silent_for_s)
        if self.tail:
            yield self.tail

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError("test fake")

    def generate_structured(self, model, messages, schema, *, temperature=0.0,
                            timeout_ms=None, cancel_event=None):
        raise NotImplementedError("test fake")

    def list_available_models(self) -> list[dict]:
        return [{"id": "fake"}]


def _coding_request(**overrides) -> DelegationRequest:
    base = {"step_id": "1", "trace_id": "t", "input": {"message": "fix it"}}
    base.update(overrides)
    return DelegationRequest(**base)


def test_coding_aborts_quiet_stream_fast(monkeypatch) -> None:
    # 5s of silence must fail in ~grace, not after the full budget.
    import app.agents.coding as coding_mod

    monkeypatch.setattr(coding_mod, "CODING_FIRST_VISIBLE_TIMEOUT_S", 0.3)
    agent = CodingAgent(SilentStreamProvider(silent_for_s=5.0))
    start = time.monotonic()
    resp = agent.execute(_coding_request(on_delta=lambda c: None))
    elapsed = time.monotonic() - start
    assert resp.status is StepStatus.FAILURE
    assert "no visible output" in (resp.error or "")
    assert elapsed < 3.0, elapsed


def test_coding_aborts_quiet_generate_fast(monkeypatch) -> None:
    # Non-streaming path gets the same guard.
    import app.agents.coding as coding_mod

    monkeypatch.setattr(coding_mod, "CODING_FIRST_VISIBLE_TIMEOUT_S", 0.3)
    agent = CodingAgent(SilentStreamProvider(silent_for_s=5.0))
    start = time.monotonic()
    resp = agent.execute(_coding_request())
    elapsed = time.monotonic() - start
    assert resp.status is StepStatus.FAILURE
    assert "no visible output" in (resp.error or "")
    assert elapsed < 3.0, elapsed


def test_coding_healthy_stream_unaffected(monkeypatch) -> None:
    import app.agents.coding as coding_mod

    monkeypatch.setattr(coding_mod, "CODING_FIRST_VISIBLE_TIMEOUT_S", 0.2)
    agent = CodingAgent(SilentStreamProvider(silent_for_s=0.0, tail="```py\nx=1\n```"))
    resp = agent.execute(_coding_request(on_delta=lambda c: None))
    assert resp.status is StepStatus.SUCCESS
    assert "x=1" in (resp.output or "")


class FlakyAgent(Agent):
    agent_id = "flaky"
    name = "Flaky"
    description = "fails after a delay, counts calls"

    def __init__(self, delay_s: float = 0.08):
        self.delay_s = delay_s
        self.calls = 0

    def execute(self, request: DelegationRequest) -> DelegationResponse:
        self.calls += 1
        time.sleep(self.delay_s)
        return DelegationResponse(
            step_id=request.step_id,
            status=StepStatus.FAILURE,
            error="boom",
        )


def _agent_step() -> PlanStep:
    return PlanStep(step_id="1", agent_id="flaky",
                    input={"message": "do it"},
                    expected_output_type="text")


def test_retry_loop_skips_doomed_attempt() -> None:
    # 80ms attempts against a 100ms budget: attempt 2 would start with ~20ms
    # left (< 1/3 of budget) — it must not launch.
    registry = AgentRegistry()
    agent = FlakyAgent(delay_s=0.08)
    registry.register(agent)
    result = _run_step_body(
        _agent_step(), "t", {"message": "do it"}, registry,
        2, 100, None, None, None, threading.Event(),
    )
    assert result.status is StepStatus.FAILURE
    assert agent.calls == 1, agent.calls


def test_retry_loop_still_retries_with_budget() -> None:
    # Generous budget: all 3 attempts run (existing behavior preserved).
    registry = AgentRegistry()
    agent = FlakyAgent(delay_s=0.01)
    registry.register(agent)
    result = _run_step_body(
        _agent_step(), "t", {"message": "do it"}, registry,
        2, 120_000, None, None, None, threading.Event(),
    )
    assert result.status is StepStatus.FAILURE
    assert result.error == "boom"
    assert agent.calls == 3, agent.calls
