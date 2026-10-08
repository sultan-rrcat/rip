"""Ollama provider: multi-model local serving over the OpenAI-compatible API.

Implements the ModelProvider contract (Charter NFR-7) against Ollama's
OpenAI-compat endpoint (ollama.com/blog/openai-compatibility, verified; the
compat layer maps response_format onto the native `format` param for
structured output — ollama.com/blog/structured-outputs).

Deltas, all contract-driven:

- MULTI-MODEL: Ollama serves any pulled model, chosen per request. Requested
  names the server actually has are honored verbatim; everything else
  resolves to `ollama_default_model`, logged once and reflected in
  served_model() so traces record what will really run.
- REMOTE HOST + PROXY TRAP: the server lives on the LAN and developer shells
  carry proxy env vars (curl needs --noproxy "*"). The httpx client therefore
  sets trust_env=False — default httpx honors HTTP(S)_PROXY, which would
  intercept this traffic.
- INIT: /api/tags is both the fail-honest health check and the source of the
  available-model set used for resolution above.

Lifecycle contract: the server is external (started by the user); this
provider owns no process and fails honestly at init if unreachable.

RIP port: no plugin system — direct ModelProvider subclass (ADR-017).
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import settings

from .base import ModelProvider
from .streaming import ThinkFilter, strip_think

logger = logging.getLogger("providers.ollama")


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    """True when the run's stop flag is set (cooperative cancel)."""
    return cancel_event is not None and cancel_event.is_set()


def _await_thread(thread: threading.Thread, cancel_event: threading.Event | None) -> None:
    """Join a background HTTP thread, aborting promptly on cancel.

    The orphaned thread keeps running to completion in the background
    (side-effect free — a single HTTP call whose result is discarded), but
    the worker thread unblocks within ~50ms so the run can unwind to the
    `cancelled` terminal state instead of waiting out the HTTP timeout.
    """
    while thread.is_alive():
        if _is_cancelled(cancel_event):
            raise RuntimeError("run cancelled")
        thread.join(timeout=0.05)


class OllamaProvider(ModelProvider):
    """Ollama provider (no plugin system — direct ModelProvider subclass)."""

    def __init__(self) -> None:
        # trust_env=False is LOAD-BEARING: default httpx honors HTTP(S)_PROXY,
        # which would route this LAN traffic through the environment proxy.
        self._client = httpx.Client(
            base_url=settings.ollama_base_url,
            timeout=settings.ollama_timeout_ms / 1000.0,
            trust_env=False,
        )
        self._last_usage: dict[str, int] | None = None
        self._last_finish_reason: str | None = None
        # Reasoning (chain-of-thought) chars streamed alongside the last
        # generation (delta.reasoning, never yielded). Lets callers tell a
        # think-burn (large reasoning, no visible text) from true silence.
        self._last_reasoning_chars: int = 0
        self._available: set[str] = set()
        self._warned: set[str] = set()
        self._reachable = False

        # Degraded boot: a 5s probe only — never raise here. Per-request paths
        # re-probe and fail honest when Ollama is still down.
        self._probe_once(timeout=5.0)

    def _probe_once(self, *, timeout: float) -> bool:
        """Single /api/tags probe; returns reachability, never raises."""
        try:
            response = self._client.get("/api/tags", timeout=timeout)
        except httpx.RequestError as e:
            logger.warning(
                "Ollama unreachable at %s: %s (degraded — per-request fail-honest)",
                settings.ollama_base_url,
                e,
            )
            self._reachable = False
            self._available = set()
            return False
        if response.status_code != 200:
            logger.warning(
                "Ollama health check status %s (degraded — per-request fail-honest)",
                response.status_code,
            )
            self._reachable = False
            self._available = set()
            return False
        try:
            self._available = {
                m["name"]
                for m in response.json().get("models", [])
                if m.get("name")
            }
        except (json.JSONDecodeError, TypeError, KeyError, AttributeError) as e:
            logger.warning(
                "Ollama /api/tags unreadable (%s); per-request model routing "
                "disabled — everything serves the configured default",
                e,
            )
            self._available = set()
        self._reachable = True
        logger.info(
            "Ollama reachable at %s (%d model(s): %s)",
            settings.ollama_base_url,
            len(self._available),
            ", ".join(sorted(self._available)) or "none listed",
        )
        return True

    def ensure_ready(self) -> None:
        """Fail-honest per-request gate; re-probes once when degraded."""
        if self._reachable:
            return
        if not self._probe_once(timeout=5.0):
            raise ValueError(
                f"Could not connect to Ollama at {settings.ollama_base_url}. "
                "Start it or fix OLLAMA_BASE_URL (see .env.example)."
            )

    @property
    def last_usage(self) -> dict[str, int] | None:
        """Token usage from the most recent generation."""
        return self._last_usage

    @property
    def last_finish_reason(self) -> str | None:
        """Finish reason (stop/length/...) from the most recent generation."""
        return self._last_finish_reason

    @property
    def last_reasoning_chars(self) -> int:
        """Reasoning chars streamed with the most recent generation."""
        return self._last_reasoning_chars

    def _resolve_model(self, requested: str) -> str:
        """Honor per-request names the server actually has; everything else
        resolves to the configured default — logged ONCE per foreign name,
        never silent, and         mirrored by served_model() for trace honesty."""
        if not self._reachable:
            return settings.ollama_default_model
        if requested in self._available:
            return requested
        if requested not in self._warned:
            self._warned.add(requested)
            logger.warning(
                "Requested model %r is not pulled on Ollama; serving %r "
                "instead (pulled models: %s)",
                requested,
                settings.ollama_default_model,
                ", ".join(sorted(self._available)) or "none",
            )
        return settings.ollama_default_model

    def _post_cancellable(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> httpx.Response:
        """Blocking POST that aborts promptly on cancel.

        Runs the httpx call on a daemon thread and polls the cancel flag
        every 50ms. On cancel the worker raises immediately; the orphaned
        thread finishes harmlessly in the background (its response is
        discarded). Without cancel this behaves exactly like client.post.
        """
        if _is_cancelled(cancel_event):
            raise RuntimeError("run cancelled")
        box: dict[str, Any] = {}

        def _do() -> None:
            try:
                if timeout is None:
                    box["response"] = self._client.post(url, json=json)
                else:
                    box["response"] = self._client.post(url, json=json, timeout=timeout)
            except Exception as e:  # noqa: BLE001 - re-raised on the worker thread
                box["error"] = e

        worker = threading.Thread(target=_do, daemon=True)
        worker.start()
        _await_thread(worker, cancel_event)
        if "error" in box:
            raise box["error"]
        return box["response"]

    def _chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        self.ensure_ready()
        payload: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Thinking models (e.g. lfm2.5) otherwise spend the token
            # budget on chain-of-thought and return empty content
            # (observed: done_reason=length, 64/64 tokens thinking).
            # Pre-thinking-era servers that reject the unknown field get
            # one retry without it below.
            "think": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        def _post(body: dict[str, Any]) -> httpx.Response:
            try:
                return self._post_cancellable(
                    "/v1/chat/completions", json=body, cancel_event=cancel_event
                )
            except RuntimeError:
                raise
            except httpx.RequestError as e:
                logger.error("Ollama request failed: %s", e)
                raise RuntimeError(f"Ollama connection failure: {e}") from e

        response = _post(payload)
        if response.status_code != 200 and "think" in response.text.lower():
            logger.warning("Ollama rejected think flag; retrying without it")
            payload = {k: v for k, v in payload.items() if k != "think"}
            response = _post(payload)
        if response.status_code != 200:
            logger.error(
                "Ollama non-200 status=%s body=%s",
                response.status_code,
                response.text,
            )
            raise RuntimeError(
                f"Ollama error [status {response.status_code}]: {response.text}"
            )
        return response.json()

    def _stream_payload(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            # think=False: thinking models must not burn the token budget
            # on chain-of-thought (see _chat). stream_options asks for a
            # trailing usage chunk so last_usage stays honest. Servers that
            # reject either unknown field hit _chat_stream's fallback.
            "think": False,
            "stream_options": {"include_usage": True},
        }
        if response_format is not None:
            payload["response_format"] = response_format
        return payload

    def _post_stream(
        self,
        payload: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield parsed SSE data events for one streaming POST.

        Raises the same honest RuntimeErrors as _chat (connection failure,
        non-200 with status + body). A `data:` line that is not JSON is
        transport noise: logged and skipped, never fatal to the stream.
        Checks `cancel_event` before every yielded line so the stop button
        interrupts an in-flight generation instead of waiting for the
        server to finish or the HTTP timeout to fire.
        """
        if _is_cancelled(cancel_event):
            raise RuntimeError("run cancelled")
        self.ensure_ready()
        try:
            with self._client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as response:
                if response.status_code != 200:
                    response.read()
                    logger.error(
                        "Ollama non-200 status=%s body=%s",
                        response.status_code,
                        response.text,
                    )
                    raise RuntimeError(
                        f"Ollama error [status {response.status_code}]: {response.text}"
                    )
                for line in response.iter_lines():
                    if _is_cancelled(cancel_event):
                        try:
                            response.close()
                        finally:
                            pass
                        raise RuntimeError("run cancelled")
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError as e:
                        logger.warning("skipping malformed SSE chunk: %s", e)
        except RuntimeError:
            raise
        except httpx.RequestError as e:
            logger.error("Ollama request failed: %s", e)
            raise RuntimeError(f"Ollama connection failure: {e}") from e

    def _chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        payload = self._stream_payload(
            model, messages,
            temperature=temperature, max_tokens=max_tokens, response_format=response_format,
        )
        try:
            yield from self._post_stream(payload, cancel_event=cancel_event)
        except RuntimeError as e:
            # Older servers reject unknown fields instead of ignoring them:
            # retry ONCE with each named field stripped (usage then stays
            # None when stream_options goes — logged, never fabricated).
            err = str(e).lower()
            if "think" in err and "think" in payload:
                logger.warning("Ollama rejected think flag; retrying without it")
                payload = {k: v for k, v in payload.items() if k != "think"}
            if "stream_options" in err and "stream_options" in payload:
                logger.warning(
                    "Ollama rejected stream_options; retrying without usage request "
                    "(token usage will be missing for this generation)"
                )
                payload = {k: v for k, v in payload.items() if k != "stream_options"}
            if "think" in payload and "stream_options" in payload:
                raise
            yield from self._post_stream(payload, cancel_event=cancel_event)

    @staticmethod
    def _delta_content(event: dict[str, Any]) -> str:
        """Text of one stream event, or "" for control/usage-only events."""
        try:
            return event["choices"][0]["delta"].get("content") or ""
        except (KeyError, IndexError, AttributeError, TypeError):
            return ""

    @staticmethod
    def _delta_reasoning(event: dict[str, Any]) -> str:
        """Chain-of-thought text of one stream event, or "" when absent.

        Thinking models on the OpenAI-compat endpoint stream reasoning in
        `delta.reasoning` (separate from `delta.content`, which stays ""
        while the model thinks). Never yielded to callers — counted for
        observability so a think-burn is distinguishable from true silence.
        """
        try:
            delta = event["choices"][0]["delta"]
        except (KeyError, IndexError, AttributeError, TypeError):
            return ""
        if not isinstance(delta, dict):
            return ""
        for key in ("reasoning", "reasoning_content", "thinking"):
            val = delta.get(key)
            if isinstance(val, str) and val:
                return val
        return ""

    @staticmethod
    def _message_reasoning(data: dict[str, Any]) -> str:
        """Reasoning text of a non-stream response, or "" when absent."""
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return ""
        if not isinstance(message, dict):
            return ""
        for key in ("reasoning", "reasoning_content", "thinking"):
            val = message.get(key)
            if isinstance(val, str) and val:
                return val
        return ""

    @staticmethod
    def _finish_reason(event_or_data: dict[str, Any]) -> str | None:
        """Finish reason for one stream event or one non-stream response."""
        try:
            choice = event_or_data["choices"][0]
        except (KeyError, IndexError, TypeError):
            return None
        if not isinstance(choice, dict):
            return None
        for key in ("finish_reason", "done_reason", "done"):
            val = choice.get(key)
            if isinstance(val, str) and val:
                return val
            if key == "done" and val is True:
                return "stop"
        # Native /api/chat shape uses top-level `done_reason`.
        done_reason = event_or_data.get("done_reason")
        if isinstance(done_reason, str) and done_reason:
            return done_reason
        if event_or_data.get("done") is True:
            return "stop"
        return None

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as e:
            logger.error("malformed Ollama response: %s", e)
            raise RuntimeError(
                f"Malformed response structure from Ollama: {e}"
            ) from e
        # Thinking models can emit <think> blocks in content; strip closed
        # blocks, and drop everything after an unclosed one (the generation
        # budget was likely exhausted inside reasoning).
        return strip_think(content)

    def _record_usage(self, data: dict[str, Any]) -> None:
        usage = data.get("usage")
        if usage is None:
            self._last_usage = None
            return
        details: dict[str, int] = {
            "input": usage.get("prompt_tokens", 0),
            "output": usage.get("completion_tokens", 0),
        }
        # Langfuse usage buckets are mutually exclusive: `input` excludes any
        # input_* sub-bucket, so cached prompt tokens must be split out.
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        if cached:
            details["input"] = max(details["input"] - cached, 0)
            details["input_cached_tokens"] = cached
        details["total"] = usage.get("total_tokens", 0)
        self._last_usage = details

    def served_model(self, requested_model: str) -> str:
        # Multi-model server: available names pass through; foreign names
        # resolve exactly like _chat resolves them, so the trace records the
        # model that will really run (never a foreign name for Ollama output).
        return self._resolve_model(requested_model)

    def generate(
        self,
        model,
        messages,
        *,
        temperature=settings.default_temperature,
        max_tokens: int | None = settings.default_max_tokens,
        cancel_event: threading.Event | None = None,
    ) -> str:
        data = self._chat(
            model,
            messages,
            temperature=temperature,
            # Never omit: an omitted max_tokens lets server defaults rule.
            max_tokens=max_tokens if max_tokens is not None else settings.default_max_tokens,
            cancel_event=cancel_event,
        )
        self._record_usage(data)
        self._last_finish_reason = self._finish_reason(data)
        self._last_reasoning_chars = len(self._message_reasoning(data))
        return self._content(data)

    def generate_stream(
        self,
        model,
        messages,
        *,
        temperature=settings.default_temperature,
        max_tokens: int | None = settings.default_max_tokens,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        # Reset first: usage is only meaningful if this stream reports it
        # (a stale value from a previous call would lie).
        self._last_usage = None
        self._last_finish_reason = None
        self._last_reasoning_chars = 0
        think = ThinkFilter()
        for event in self._chat_stream(
            model,
            messages,
            temperature=temperature,
            # Never omit: an omitted max_tokens lets server defaults rule.
            max_tokens=max_tokens if max_tokens is not None else settings.default_max_tokens,
            cancel_event=cancel_event,
        ):
            if event.get("usage") is not None:
                self._record_usage(event)
            reason = self._finish_reason(event)
            if reason:
                self._last_finish_reason = reason
            reasoning = self._delta_reasoning(event)
            if reasoning:
                self._last_reasoning_chars += len(reasoning)
            visible = think.feed(self._delta_content(event))
            if visible:
                yield visible
        # A failed stream raises out of the loop above, so the tail flush
        # only runs on success (no trailing partial on failure).
        tail = think.close()
        if tail:
            yield tail

    def generate_structured(
        self,
        model,
        messages,
        schema,
        *,
        temperature: float = 0.0,
        timeout_ms: int | None = None,
        cancel_event: threading.Event | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        # Native /api/chat with the RAW schema as `format` — NOT the /v1
        # OpenAI wrapper. The compat layer's response_format mapping proved
        # lossy on minicpm5-2b (malformed plans: stray step_ids, empty inputs),
        # while native format went 4/4 VALID on the real planner prompt
        # (2026-09-13 probes). think=False explicit: this tag rejects
        # think:true ("does not support thinking").
        self.ensure_ready()
        self._last_reasoning_chars = 0
        self._last_finish_reason = None
        payload: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {
                "temperature": temperature,
                # Per-call output cap (None = shared default): thinking
                # burns the same num_predict budget as the answer, so tiny
                # control-plane outputs carry tight caps (router 256).
                "num_predict": (
                    max_tokens if max_tokens is not None
                    else settings.default_max_tokens
                ),
            },
            "think": False,
        }
        # Per-call deadline: control-plane callers (router, ReAct planner) pass a
        # tight budget so a saturated server fails them open fast instead of
        # blocking for the full client timeout. None keeps the client default.
        request_timeout = (
            timeout_ms / 1000.0 if timeout_ms is not None else None
        )
        try:
            response = self._post_cancellable(
                "/api/chat", json=payload, timeout=request_timeout,
                cancel_event=cancel_event,
            )
        except RuntimeError:
            raise
        except httpx.RequestError as e:
            logger.error("Ollama request failed: %s", e)
            raise RuntimeError(f"Ollama connection failure: {e}") from e
        if response.status_code != 200:
            logger.error(
                "Ollama non-200 status=%s body=%s",
                response.status_code,
                response.text,
            )
            raise RuntimeError(
                f"Ollama error [status {response.status_code}]: {response.text}"
            )
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Malformed Ollama response (not JSON): {e}") from e
        # Native usage counters differ from the OpenAI shape: map honestly,
        # never fabricate.
        prompt_count = data.get("prompt_eval_count")
        eval_count = data.get("eval_count")
        if prompt_count is not None or eval_count is not None:
            prompt_count = prompt_count or 0
            eval_count = eval_count or 0
            self._last_usage = {
                "input": prompt_count,
                "output": eval_count,
                "total": prompt_count + eval_count,
            }
        else:
            self._last_usage = None
        self._last_finish_reason = self._finish_reason(data)
        self._last_reasoning_chars = len(self._message_reasoning(data))
        try:
            content = data["message"]["content"].strip()
        except (KeyError, AttributeError, TypeError) as e:
            logger.error("malformed Ollama response: %s", e)
            raise RuntimeError(
                f"Malformed response structure from Ollama: {e}"
            ) from e
        content = strip_think(content)
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Failed to parse structured output: {e}") from e

    def embed(self, model: str, text: str) -> list[float]:
        raise NotImplementedError(
            "Ollama provider is text-generation only; embeddings are not "
            "supported yet (Ollama can serve them via /api/embed if/when the "
            "RAG agent needs it — deliberately deferred)."
        )

    def list_available_models(self) -> list[dict]:
        fallback = [
            {
                "id": settings.ollama_default_model,
                "display_name": settings.ollama_default_model,
            }
        ]
        try:
            response = self._client.get("/api/tags")
            if response.status_code != 200:
                logger.error(
                    "model listing non-200 status=%s body=%s",
                    response.status_code,
                    response.text,
                )
                return fallback
            data = response.json()
        except (httpx.RequestError, json.JSONDecodeError) as e:
            logger.error(
                "model listing failed, falling back to configured model: %s", e
            )
            return fallback
        models = [
            {"id": m.get("name"), "display_name": m.get("name")}
            for m in data.get("models", [])
            if m.get("name")
        ]
        if not models:
            models.append(fallback[0])
        return models
