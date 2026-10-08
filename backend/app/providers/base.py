"""ModelProvider interface.

This is the portability boundary (Charter NFR-7): everything above this layer
(Router, Builders, ReAct, Execution Engine, agents) talks ONLY to this interface.
Ollama is the sole backend (ADR-003 superseded); future providers implement it
behind this contract.

Operations needed across the system:
- generate:          plain text completion (Reasoning chat)
- generate_structured: JSON output matching a schema (Router intent, ReAct steps, rag query decomposition)
- embed:             vector embedding (RAG / retrieval, added at PM-5)
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class ModelProvider(ABC):
    @abstractmethod
    def generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Return the model's text response to `messages` (OpenAI-style message list).

        `cancel_event` is the run's cooperative cancel flag: providers MUST
        abort promptly (raising RuntimeError) when it is set, so the stop
        button interrupts an in-flight LLM call instead of waiting for the
        HTTP timeout. None = run without cancellation (tests, ad-hoc).
        """

    def generate_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        """Yield the model's text response as incremental text chunks.

        Chunks are non-empty text fragments; their concatenation is the same
        answer `generate()` would return. Providers with native token
        streaming override this; the default is honest (one chunk), so every
        existing subclass keeps working unchanged.

        Concrete (not abstract) ON PURPOSE: test fakes and future providers
        that only implement generate() must keep instantiating.
        """
        yield self.generate(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, cancel_event=cancel_event,
        )

    @abstractmethod
    def generate_structured(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        temperature: float = 0.0,
        timeout_ms: int | None = None,
        cancel_event: threading.Event | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return a JSON object (as a Python dict) conforming to `schema`.

        `timeout_ms` bounds THIS call only (None = provider default). The
        control-plane callers pass a tight deadline: router and ReAct planner
        emit tiny JSON, so inheriting the full generation budget let a saturated
        Ollama hold a run hostage for the whole ollama_timeout_ms window.
        `cancel_event` aborts the call promptly (same contract as generate).
        `max_tokens` caps generated tokens for THIS call (None = provider
        default): with thinking models the chain-of-thought burns the same
        budget as the answer, so tiny outputs (intent, sub-queries) carry a
        tight cap while variable-length ones (filter quotes) ride the default.
        """

    @abstractmethod
    def embed(self, model: str, text: str) -> list[float]:
        """Return the embedding vector for `text`."""

    @abstractmethod
    def list_available_models(self) -> list[dict]:
        """Every provider must answer 'Which models can you generate with?'"""

    def served_model(self, requested_model: str) -> str:
        """The model that will actually serve this request.

        Defaults to the requested model. Backends that ignore per-capability
        routing (single-model servers) override this so traces record what
        really ran, not what was asked for.
        """
        return requested_model
