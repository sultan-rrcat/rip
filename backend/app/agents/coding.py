from __future__ import annotations

import logging
import time
from typing import ClassVar

from app.agents.base import Agent, DelegationRequest, DelegationResponse, StepStatus
from app.core import constants
from app.core.config import settings
from app.providers.base import ModelProvider

logger = logging.getLogger("agents.coding")

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


class CodingAgent(Agent):
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

    def __init__(self, provider: ModelProvider):
        self._provider = provider

    def execute(self, request: DelegationRequest) -> DelegationResponse:
        start = time.perf_counter()
        logger.info("coding start step=%s trace=%s", request.step_id, request.trace_id)
        try:
            message = request.input.get("message")
            if not message:
                logger.warning("coding missing message step=%s", request.step_id)
                return DelegationResponse(
                    step_id=request.step_id,
                    status=StepStatus.FAILURE,
                    output=None,
                    confidence=constants.CONFIDENCE_LOW,
                    error="Execution failed: 'message' is required in input",
                )
            history = request.input.get("history", [])
            context = request.input.get("context")

            messages = [{"role": "system", "content": CODING_SYSTEM_PROMPT}]
            if context:
                messages.append({"role": "system", "content": f"Conversation context:\n{context}"})
            messages.extend(
                {"role": turn["role"], "content": turn["content"]}
                for turn in history
                if isinstance(turn, dict) and "role" in turn and "content" in turn
            )
            messages.append({"role": "user", "content": message})

            # Coding emits whole files + test scripts: per-step override
            # when the plan sets one, else the generous coding budget
            # (never the shared default — 2048 truncates test files).
            budget = request.max_tokens or settings.coding_max_tokens

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

            # Empty output is a failure, not a blank answer: SUCCESS with ""
            # renders as an empty chat bubble (and skips the retry loop),
            # while FAILURE retries honestly and surfaces the error.
            if not (output_text or "").strip():
                logger.warning("coding empty output step=%s", request.step_id)
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
            logger.exception("coding failed step=%s", request.step_id)
            result = DelegationResponse(
                step_id=request.step_id,
                status=StepStatus.FAILURE,
                output=None,
                confidence=constants.CONFIDENCE_LOW,
                error=f"Execution failed: {e!s}",
            )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "coding done step=%s status=%s duration_ms=%.0f",
            request.step_id, result.status.value, duration_ms,
        )
        return result
