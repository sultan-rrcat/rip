"""ProviderAgent — shared base for agents that delegate to a ModelProvider.

Holds the common execute logic: message extraction, history/context
assembly, provider call (streaming or not), empty-output-as-failure,
exception wrapping, duration logging. Subclasses specify only:
- ``system_prompt``: the agent's voice
- ``default_budget``: the agent's default max_tokens
- ``_start_watchdog``: optional first-visible-token guard
- ``_handle_empty_output``: agent-specific empty-output handling
"""

from __future__ import annotations

import logging
import threading
import time
from abc import abstractmethod
from typing import ClassVar

from app.agents.base import Agent, DelegationRequest, DelegationResponse, StepStatus
from app.core import constants
from app.providers.base import ModelProvider

logger = logging.getLogger("agents")


class _Watchdog:
    """First-visible-token guard for generation attempts.

    Ornith-class models intermittently burn a whole max_tokens budget
    (~145s at 4096) streaming think-only/empty content that the
    ThinkFilter strips to "". Aborting the attempt fast leaves retry
    budget inside the step wall-clock instead of orphaning a late
    success. Once the first visible token arrives the guard is met —
    total time stays bounded by the outer wall clock.
    """

    def __init__(self, timeout_s: float, cancel_event: threading.Event | None):
        self._timeout_s = timeout_s
        self._cancel_event = cancel_event
        self._stop = threading.Event()
        self._state = {"visible": False, "quiet_timeout": False}
        self._started = time.monotonic()
        self._thread: threading.Thread | None = None

    def start(self) -> threading.Event:
        """Start the watchdog and return the cancel event to use."""
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self._stop

    def mark_visible(self) -> None:
        """Mark that a visible token has arrived."""
        self._state["visible"] = True

    def check_trip(self) -> bool:
        """Check if the watchdog tripped (for RuntimeError handling)."""
        return self._state["quiet_timeout"]

    def stop(self) -> None:
        """Stop the watchdog thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _watch(self) -> None:
        while not self._stop.is_set():
            if self._cancel_event is not None and self._cancel_event.is_set():
                self._stop.set()
                return
            if not self._state["visible"] and (
                time.monotonic() - self._started
            ) > self._timeout_s:
                self._state["quiet_timeout"] = True
                self._stop.set()
                return
            time.sleep(0.5)


class ProviderAgent(Agent):
    """Base class for agents that delegate to a ModelProvider.

    Subclasses must define:
    - ``system_prompt``: the agent's voice (ClassVar[str])
    - ``default_budget``: the agent's default max_tokens (ClassVar[int])

    Subclasses may override:
    - ``_start_watchdog``: return a _Watchdog instance or None
    - ``_handle_empty_output``: agent-specific empty-output handling
    """

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """The agent's system prompt. Must be overridden by subclasses."""

    @property
    @abstractmethod
    def default_budget(self) -> int:
        """The agent's default max_tokens. Must be overridden by subclasses."""

    def __init__(self, provider: ModelProvider):
        self._provider = provider

    def execute(self, request: DelegationRequest) -> DelegationResponse:
        start = time.perf_counter()
        logger.info(
            "%s start step=%s trace=%s",
            self.agent_id, request.step_id, request.trace_id,
        )
        try:
            # 1. Pull `message` (and optional `history`) from request.input
            message = request.input.get("message")
            if not message:
                logger.warning(
                    "%s missing message step=%s", self.agent_id, request.step_id
                )
                return DelegationResponse(
                    step_id=request.step_id,
                    status=StepStatus.FAILURE,
                    output=None,
                    confidence=constants.CONFIDENCE_LOW,
                    error="Execution failed: 'message' is required in input",
                )
            history = request.input.get("history", [])
            context = request.input.get("context")

            # 2. Build the OpenAI-style `messages` list: system voice first,
            #    then conversation context (short-term memory), history, current
            messages = [{"role": "system", "content": self.system_prompt}]
            if context:
                messages.append(
                    {"role": "system", "content": f"Conversation context:\n{context}"}
                )
            messages.extend(
                {"role": turn["role"], "content": turn["content"]}
                for turn in history
                if isinstance(turn, dict) and "role" in turn and "content" in turn
            )
            messages.append({"role": "user", "content": message})

            # Per-step budget when the plan sets one; otherwise the agent default.
            budget = request.max_tokens or self.default_budget

            # 3. Call the provider (streaming when the caller wants deltas).
            #    The run's cancel_event rides along so the stop button
            #    interrupts an in-flight generation instead of waiting out
            #    the HTTP timeout.
            cancel_event = request.cancel_event
            watchdog = self._start_watchdog(request)
            if watchdog is not None:
                cancel_event = watchdog.start()
            try:
                if request.on_delta is not None:
                    parts: list[str] = []
                    for chunk in self._provider.generate_stream(
                        model=self._model,
                        messages=messages,
                        temperature=self._temperature,
                        max_tokens=budget,
                        cancel_event=cancel_event,
                    ):
                        if watchdog is not None:
                            watchdog.mark_visible()
                        parts.append(chunk)
                        request.on_delta(chunk)
                    output_text = "".join(parts)
                else:
                    output_text = self._provider.generate(
                        model=self._model,
                        messages=messages,
                        temperature=self._temperature,
                        max_tokens=budget,
                        cancel_event=cancel_event,
                    )
            except RuntimeError:
                if watchdog is not None and watchdog.check_trip():
                    return self._handle_watchdog_trip(
                        request, watchdog, start
                    )
                raise
            finally:
                if watchdog is not None:
                    watchdog.stop()

            # 4. Return success + output + confidence. Empty output is a
            #    failure, not a blank answer: SUCCESS with "" renders as an
            #    empty chat bubble (and skips the retry loop), while FAILURE
            #    retries honestly and surfaces the error to the user.
            if not (output_text or "").strip():
                return self._handle_empty_output(request, start)
            return DelegationResponse(
                step_id=request.step_id,
                status=StepStatus.SUCCESS,
                output=output_text,
                confidence=constants.CONFIDENCE_SUCCESS,
            )
        except Exception as e:
            # 5. Wrap the provider call so ANY exception becomes a failure response.
            logger.exception("%s failed step=%s", self.agent_id, request.step_id)
            result = DelegationResponse(
                step_id=request.step_id,
                status=StepStatus.FAILURE,
                output=None,
                confidence=constants.CONFIDENCE_LOW,
                error=f"Execution failed: {e!s}",
            )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s done step=%s status=%s duration_ms=%.0f",
            self.agent_id, request.step_id, result.status.value, duration_ms,
        )
        return result

    @property
    def _model(self) -> str:
        from app.core.config import settings
        return settings.ollama_default_model

    @property
    def _temperature(self) -> float:
        from app.core.config import settings
        return settings.default_temperature

    def _start_watchdog(
        self, request: DelegationRequest
    ) -> _Watchdog | None:
        """Override to add a watchdog. Default: no watchdog."""
        return None

    def _handle_empty_output(
        self, request: DelegationRequest, start: float
    ) -> DelegationResponse:
        """Handle empty output. Override for agent-specific handling."""
        logger.warning(
            "%s empty output step=%s", self.agent_id, request.step_id
        )
        return DelegationResponse(
            step_id=request.step_id,
            status=StepStatus.FAILURE,
            output=None,
            confidence=constants.CONFIDENCE_LOW,
            error="Execution failed: model returned no text",
        )

    def _handle_watchdog_trip(
        self, request: DelegationRequest, watchdog: _Watchdog, start: float
    ) -> DelegationResponse:
        """Handle a watchdog trip. Override for agent-specific handling."""
        logger.warning(
            "%s watchdog trip step=%s", self.agent_id, request.step_id
        )
        result = DelegationResponse(
            step_id=request.step_id,
            status=StepStatus.FAILURE,
            output=None,
            confidence=constants.CONFIDENCE_LOW,
            error="Execution failed: model streamed no visible output",
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s done step=%s status=%s duration_ms=%.0f",
            self.agent_id, request.step_id, result.status.value, duration_ms,
        )
        return result