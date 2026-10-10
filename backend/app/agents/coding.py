from __future__ import annotations

import logging
import time
from typing import ClassVar

from app.agents.base import DelegationRequest, DelegationResponse, StepStatus
from app.agents.provider_agent import ProviderAgent, _Watchdog
from app.core import constants
from app.providers.base import ModelProvider

logger = logging.getLogger("agents.coding")

#: Time-to-first-visible-token guard for generation attempts. Ornith-class
#: models intermittently burn a whole max_tokens budget (~145s at 4096)
#: streaming think-only/empty content that the ThinkFilter strips to "".
#: Aborting the attempt fast leaves retry budget inside the step
#: wall-clock instead of orphaning a late success (trace b4301187: two
#: ~145s empties, then attempt 3's success discarded past the 300s
#: deadline). Once the first visible token arrives the guard is met —
#: total time stays bounded by the outer wall clock.
CODING_FIRST_VISIBLE_TIMEOUT_S = 60.0

#: Code-specialized voice: same execution shape as ReasoningAgent, but the
#: system prompt is tuned for code generation / explanation. Generate-and-
#: present only — no execution, no sandbox (deferred).
CODING_SYSTEM_PROMPT = (
    "You are RIP's coding agent, an expert software engineer working in any "
    "programming language. Answer the user directly in clear, well-formatted "
    "Markdown: lead with the requested code in fenced code blocks (correct "
    "language tag per block), then a brief explanation (approach, usage, "
    "edge cases). When the request needs several files (e.g. an HTML page "
    "with CSS and JS), emit one fenced block per file with a short heading "
    "naming each file. When no files are attached, write the code from "
    "scratch — complete and runnable. Keep prose concise. Never expose "
    "internal machinery (step ids, placeholders like {{1}}, chunk ids, "
    "model or tool names). If provided file content is truncated or missing, "
    "say so honestly instead of inventing the rest."
)


def _provider_finish_reason(provider: ModelProvider) -> str | None:
    """Finish reason of the provider's last generation, if exposed."""
    reason = getattr(provider, "last_finish_reason", None)
    if callable(reason):
        try:
            reason = reason()
        except Exception:  # noqa: BLE001 - tracing must never break runs
            return None
    return reason if isinstance(reason, str) else None


def _provider_reasoning_chars(provider: ModelProvider) -> int:
    """Reasoning chars streamed with the provider's last generation."""
    chars = getattr(provider, "last_reasoning_chars", None)
    if callable(chars):
        try:
            chars = chars()
        except Exception:  # noqa: BLE001 - tracing must never break runs
            return 0
    return chars if isinstance(chars, int) else 0


class CodingAgent(ProviderAgent):
    agent_id = "coding"
    name = "Coding Agent"
    description = (
        "Any software task in any language: write new code from scratch "
        "(web pages, apps, scripts) or explain, review, debug, test, or "
        "modify uploaded code files. Generate-and-present only, no execution."
    )
    input_schema: ClassVar[dict] = {"message": "str", "history": "optional list of {role, content}"}
    requires_permission = False
    side_effecting = False
    cost_class = "low"

    system_prompt = CODING_SYSTEM_PROMPT

    @property
    def default_budget(self) -> int:
        # Live read (see ReasoningAgent): admin budget edits apply to the
        # next step without a restart.
        from app.core.config import settings

        return settings.coding_max_tokens

    def __init__(self, provider: ModelProvider):
        super().__init__(provider)

    def _start_watchdog(self, request: DelegationRequest) -> _Watchdog | None:
        return _Watchdog(CODING_FIRST_VISIBLE_TIMEOUT_S, request.cancel_event)

    def _handle_empty_output(
        self, request: DelegationRequest, start: float
    ) -> DelegationResponse:
        finish_reason = _provider_finish_reason(self._provider)
        reasoning_chars = _provider_reasoning_chars(self._provider)
        if finish_reason == "length":
            logger.warning(
                "coding output limit with no visible text step=%s "
                "(reasoning_chars=%d)",
                request.step_id, reasoning_chars,
            )
            return DelegationResponse(
                step_id=request.step_id,
                status=StepStatus.FAILURE,
                output=None,
                confidence=constants.CONFIDENCE_LOW,
                error=(
                    "Execution failed: model hit its output limit "
                    "before answering (finish_reason=length, no "
                    "visible text"
                    + (
                        f", {reasoning_chars} thinking chars streamed"
                        if reasoning_chars else ""
                    )
                    + ")"
                ),
            )
        return super()._handle_empty_output(request, start)

    def _handle_watchdog_trip(
        self, request: DelegationRequest, watchdog: _Watchdog, start: float
    ) -> DelegationResponse:
        reasoning_chars = _provider_reasoning_chars(self._provider)
        logger.warning(
            "coding no visible output within %.0fs step=%s "
            "(reasoning_chars=%d)",
            CODING_FIRST_VISIBLE_TIMEOUT_S, request.step_id,
            reasoning_chars,
        )
        result = DelegationResponse(
            step_id=request.step_id,
            status=StepStatus.FAILURE,
            output=None,
            confidence=constants.CONFIDENCE_LOW,
            error=(
                "Execution failed: model streamed no visible "
                f"output within {CODING_FIRST_VISIBLE_TIMEOUT_S:.0f}s"
                + (
                    f" ({reasoning_chars} thinking chars streamed, "
                    "budget burned in chain-of-thought)"
                    if reasoning_chars else ""
                )
            ),
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "coding done step=%s status=%s duration_ms=%.0f",
            request.step_id, result.status.value, duration_ms,
        )
        return result