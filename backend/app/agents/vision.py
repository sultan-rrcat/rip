"""Vision Agent — analyzes images, documents, and visual content.

Mirrors ReasoningAgent's shape with a vision-specific system prompt and the
shared Ollama default model. RIP port: no plugin system — direct Agent
subclass. NOTE: this prototype passes TEXT only through ModelProvider;
image/multimodal input plumbing is a follow-up — the agent contract is
unchanged either way.
"""
from __future__ import annotations

import logging
import time

from app.agents.base import Agent, DelegationRequest, DelegationResponse, StepStatus
from app.core import constants
from app.core.config import settings
from app.providers.base import ModelProvider

logger = logging.getLogger("agents.vision")

_SYSTEM_PROMPT = (
    "You are a visual analysis expert. Describe, interpret, and extract "
    "information from images and documents accurately and concisely."
)


class VisionAgent(Agent):
    agent_id = "vision"
    name = "Vision Agent"
    description = (
        "Analyze and interpret images, diagrams, and visual content."
    )
    input_schema = {"message": "str"}
    requires_permission = False
    side_effecting = False
    cost_class = "medium"

    def __init__(self, provider: ModelProvider):
        self._provider = provider

    def execute(self, request: DelegationRequest) -> DelegationResponse:
        start = time.perf_counter()
        logger.info("vision start step=%s trace=%s", request.step_id, request.trace_id)
        try:
            # 1. Pull `message` (and optional `history`) from request.input
            message = request.input.get("message")
            if not message:
                logger.warning("vision: missing message step=%s", request.step_id)
                return DelegationResponse(
                    step_id=request.step_id,
                    status=StepStatus.FAILURE,
                    output=None,
                    confidence=constants.CONFIDENCE_LOW,
                    error="Execution failed: 'message' is required in input",
                )
            history = request.input.get("history", [])
            context = request.input.get("context")

            # 2. Build the OpenAI-style `messages` list: system prompt,
            #    conversation context (short-term memory), history, then current
            messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
            if context:
                messages.append({"role": "system", "content": f"Conversation context:\n{context}"})
            messages.extend(
                {"role": turn["role"], "content": turn["content"]}
                for turn in history
                if isinstance(turn, dict) and "role" in turn and "content" in turn
            )
            messages.append({"role": "user", "content": message})

            # 3. Call the provider (streaming when the caller wants deltas)
            if request.on_delta is not None:
                parts: list[str] = []
                for chunk in self._provider.generate_stream(
                    model=settings.ollama_default_model,
                    messages=messages,
                    temperature=settings.default_temperature,
                    max_tokens=settings.default_max_tokens,
                ):
                    parts.append(chunk)
                    request.on_delta(chunk)
                output_text = "".join(parts)
            else:
                output_text = self._provider.generate(
                    model=settings.ollama_default_model,
                    messages=messages,
                    temperature=settings.default_temperature,
                    max_tokens=settings.default_max_tokens,
                )

            # 4. Return success + output + confidence
            result = DelegationResponse(
                step_id=request.step_id,
                status=StepStatus.SUCCESS,
                output=output_text,
                confidence=constants.CONFIDENCE_SUCCESS,
            )
        except Exception as e:
            # 5. Wrap the provider call so ANY exception becomes a failure response.
            logger.exception("vision failed step=%s", request.step_id)
            result = DelegationResponse(
                step_id=request.step_id,
                status=StepStatus.FAILURE,
                output=None,
                confidence=constants.CONFIDENCE_LOW,
                error=f"Execution failed: {str(e)}",
            )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "vision done step=%s status=%s duration_ms=%.0f",
            request.step_id, result.status.value, duration_ms,
        )
        return result
