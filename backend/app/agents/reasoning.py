from __future__ import annotations

import logging
import time
from typing import ClassVar

from app.agents.base import Agent, DelegationRequest, DelegationResponse, StepStatus
from app.core import constants
from app.core.config import settings
from app.providers.base import ModelProvider

logger = logging.getLogger("agents.reasoning")

#: User-facing voice for every reasoning output (chat, grounded QA,
#: compare/summarize/quiz writers, ReAct synthesis). All of these render
#: directly in chat, so this single system prompt is the presentation lever.
REASONING_SYSTEM_PROMPT = (
    "You are RIP, a world-class research assistant. Answer the user "
    "directly in clear, friendly, well-formatted Markdown: lead with the "
    "answer, then supporting detail; use short headings, bullets, numbered "
    "steps, or a table when it helps readability; keep code spans for file "
    "and section names. Be concise but complete. Cite document sections by "
    "name when evidence was provided. Never expose internal machinery "
    "(step ids, placeholders like {{1}}, chunk ids, model or tool names). "
    "If the evidence does not cover something, say what is missing honestly."
)

class ReasoningAgent(Agent):
    agent_id = "reasoning"
    name = "Reasoning Agent"
    description = (
        "General conversational assistance, explanation, and brainstorming; "
        "fallback when no specialized capability fits."
    )
    input_schema: ClassVar[dict] = {"message": "str", "history": "optional list of {role, content}"}
    requires_permission = False
    side_effecting = False
    cost_class = "low"

    def __init__(self, provider: ModelProvider):
        self._provider = provider

    def execute(self, request: DelegationRequest) -> DelegationResponse:
        start = time.perf_counter()
        logger.info("reasoning start step=%s trace=%s", request.step_id, request.trace_id)
        try:
            # 1. Pull `message` (and optional `history`) from request.input
            message = request.input.get("message")
            if not message:
                logger.warning("reasoning missing message step=%s", request.step_id)
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
            messages = [{"role": "system", "content": REASONING_SYSTEM_PROMPT}]
            if context:
                messages.append({"role": "system", "content": f"Conversation context:\n{context}"})
            messages.extend(
                {"role": turn["role"], "content": turn["content"]}
                for turn in history
                if isinstance(turn, dict) and "role" in turn and "content" in turn
            )
            messages.append({"role": "user", "content": message})

            # Per-step budget when the plan sets one (chat steps are capped
            # low by the builder); otherwise the shared default.
            budget = request.max_tokens or settings.default_max_tokens

            # 3. Call the provider (streaming when the caller wants deltas).
            # The run's cancel_event rides along so the stop button
            # interrupts an in-flight generation instead of waiting out
            # the HTTP timeout.
            cancel_event = request.cancel_event
            if request.on_delta is not None:
                parts: list[str] = []
                for chunk in self._provider.generate_stream(
                    model=settings.ollama_default_model,
                    messages=messages,
                    temperature=settings.default_temperature,
                    max_tokens=budget,
                    cancel_event=cancel_event,
                ):
                    parts.append(chunk)
                    request.on_delta(chunk)
                output_text = "".join(parts)
            else:
                output_text = self._provider.generate(
                    model=settings.ollama_default_model,
                    messages=messages,
                    temperature=settings.default_temperature,
                    max_tokens=budget,
                    cancel_event=cancel_event,
                )

            # 4. Return success + output + confidence. Empty output is a
            # failure, not a blank answer: SUCCESS with "" renders as an
            # empty chat bubble (and skips the retry loop), while FAILURE
            # retries honestly and surfaces the error to the user.
            if not (output_text or "").strip():
                logger.warning("reasoning empty output step=%s", request.step_id)
                result = DelegationResponse(
                    step_id=request.step_id,
                    status=StepStatus.FAILURE,
                    output=None,
                    confidence=constants.CONFIDENCE_LOW,
                    error="Execution failed: model returned no text",
                )
            else:
                result = DelegationResponse(
                    step_id=request.step_id,
                    status=StepStatus.SUCCESS,
                    output=output_text,
                    confidence=constants.CONFIDENCE_SUCCESS,
                )
        except Exception as e:
            # 5. Wrap the provider call so ANY exception becomes a failure response.
            logger.exception("reasoning failed step=%s", request.step_id)
            result = DelegationResponse(
                step_id=request.step_id,
                status=StepStatus.FAILURE,
                output=None,
                confidence=constants.CONFIDENCE_LOW,
                error=f"Execution failed: {e!s}",
            )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "reasoning done step=%s status=%s duration_ms=%.0f",
            request.step_id, result.status.value, duration_ms,
        )
        return result
