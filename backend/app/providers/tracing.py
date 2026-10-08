"""Tracing wrapper around ModelProvider — ported from Athena.

Records every LLM call (generate / generate_stream / generate_structured)
as a Langfuse generation, nested under whatever trace/span is active
(run -> step). Transparently delegates to the inner provider; zero overhead
when tracing is disabled (observe() is an identity decorator then).

Each generation carries the served model name and invocation parameters,
plus input/output token counts when the inner provider exposes them
(OllamaProvider.last_usage). Input is deliberately set to the outbound
messages, not the whole decorated function signature.

RIP adaptation: no plugin system — wrap the concrete OllamaProvider via
wrap_provider() at composition time (main.py lifespan + deps lazy
fallback). Test doubles installed through deps.configure() are never
wrapped.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.core.config import settings
from app.observability.langfuse import manual_generation, observe, update_generation
from app.providers.base import ModelProvider


def wrap_provider(provider: ModelProvider) -> ModelProvider:
    """Wrap once for tracing when enabled; passthrough otherwise.

    Idempotent: an already-wrapped provider is returned as-is, so lifespan
    and lazy paths can both call this without double-wrapping.
    """
    if isinstance(provider, TracingProvider):
        return provider
    if not settings.langfuse_enabled:
        return provider
    return TracingProvider(provider)


class TracingProvider(ModelProvider):
    def __init__(self, inner: ModelProvider):
        self._inner = inner

    def _inner_usage(self) -> dict[str, int] | None:
        inner = self._inner
        usage = getattr(inner, "last_usage", None)
        if callable(usage):
            usage = usage()
        if isinstance(usage, dict):
            return {k: int(v) for k, v in usage.items() if isinstance(v, (int, float))}
        return None

    def _inner_finish_reason(self) -> str | None:
        inner = self._inner
        reason = getattr(inner, "last_finish_reason", None)
        if callable(reason):
            reason = reason()
        return reason if isinstance(reason, str) else None

    def _inner_reasoning_chars(self) -> int | None:
        inner = self._inner
        chars = getattr(inner, "last_reasoning_chars", None)
        if callable(chars):
            chars = chars()
        return chars if isinstance(chars, int) and chars else None

    def _result_metadata(self) -> dict[str, str] | None:
        meta: dict[str, str] = {}
        reason = self._inner_finish_reason()
        if reason:
            meta["finish_reason"] = reason
        chars = self._inner_reasoning_chars()
        if chars:
            meta["reasoning_chars"] = str(chars)
        return meta or None

    @observe(
        name="llm.generate", as_type="generation",
        capture_input=False, capture_output=False,
    )
    def generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        cancel_event=None,
    ) -> str:
        params: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        update_generation(
            input=messages,
            model=self._inner.served_model(model),
            model_parameters=params,
            metadata={"requested_model": model},
        )
        try:
            result = self._inner.generate(
                model=model, messages=messages,
                temperature=temperature, max_tokens=max_tokens,
                cancel_event=cancel_event,
            )
            update_generation(
                output=result,
                usage_details=self._inner_usage(),
                metadata=self._result_metadata(),
            )
            return result
        except Exception as e:  # pragma: no cover - defensive surrogate
            update_generation(status_message=str(e))
            raise

    def generate_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        cancel_event=None,
    ) -> Iterator[str]:
        """Passthrough stream with ONE end-of-stream generation update.

        NOT @observe: on a generator function the span would close when the
        wrapper returns — before the first token — capturing empty
        output/usage. And NOT start_as_current_observation either: the
        generator yields control to its consumer between chunks, and a
        "current" generation would mis-parent whatever the consumer does in
        between. manual_generation() parents to the trace without ever being
        current.

        The caller MUST fully consume the stream: usage is read from the
        inner provider only after exhaustion, and the generation ends when
        the block exits (abandoning the generator leaves it open).
        """
        params: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        with manual_generation(
            "llm.generate_stream",
            input=messages,
            model=self._inner.served_model(model),
            model_parameters=params,
            metadata={"requested_model": model, "stream": True},
        ) as gen:
            chunks: list[str] = []
            try:
                for chunk in self._inner.generate_stream(
                    model=model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    cancel_event=cancel_event,
                ):
                    chunks.append(chunk)
                    yield chunk
            except Exception as e:
                gen.update(status_message=str(e))
                raise
            gen.update(
                output="".join(chunks),
                usage_details=self._inner_usage(),
                metadata=self._result_metadata(),
            )

    @observe(
        name="llm.generate_structured", as_type="generation",
        capture_input=False, capture_output=False,
    )
    def generate_structured(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        temperature: float = 0.0,
        timeout_ms: int | None = None,
        cancel_event=None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        update_generation(
            input=messages,
            model=self._inner.served_model(model),
            model_parameters=params,
            metadata={"output_schema": True, "requested_model": model},
        )
        try:
            result = self._inner.generate_structured(
                model=model,
                messages=messages,
                schema=schema,
                temperature=temperature,
                timeout_ms=timeout_ms,
                cancel_event=cancel_event,
                max_tokens=max_tokens,
            )
            update_generation(
                output=result,
                usage_details=self._inner_usage(),
                metadata=self._result_metadata(),
            )
            return result
        except Exception as e:  # pragma: no cover - defensive surrogate
            update_generation(status_message=str(e))
            raise

    def embed(self, model: str, text: str) -> list[float]:
        return self._inner.embed(model, text)

    def list_available_models(self) -> list[dict]:
        return self._inner.list_available_models()
