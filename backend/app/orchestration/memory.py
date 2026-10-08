"""Bounded short-term conversational memory (Q28 port).

Keeps a sliding window of the newest turns verbatim and maintains a rolling
summary of everything that aged out of the window, capped by a token budget.

Q28 locked values: WINDOW_SIZE=10 (advisory verbatim window; the real
constraint is the budget), token estimator len(text)//4 (no tiktoken),
budget = int(ollama_context_window * summary_threshold_pct) (default
int(32768 * 0.7)), summary LLM call capped at summary_max_tokens via
ollama_default_model (never gemini_model_*).

The summary is incremental: each call folds only the turns that newly aged
out into the stored summary (O(1) per turn), and the caller persists it back
to notebooks.conversation_summary + summary_message_count via the folded
count (prevents double-counting as the conversation grows). This is internal
memory state — distinct from the SSE `summary` event (final answer text).
"""
from __future__ import annotations

import logging
import threading

from pydantic import BaseModel, Field

from app.core.config import settings
from app.providers.base import ModelProvider

logger = logging.getLogger("orchestration.memory")

WINDOW_SIZE = 10

#: Narrow schema for the rolling summary: a single bullet-text field.
#: Structured output rides the native endpoint where think=False is
#: honored, so the 512-token summary budget is spent on the summary
#: instead of chain-of-thought.
_SUMMARY_SCHEMA: dict = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def _default_budget_tokens() -> int:
    """Context-window-derived budget (read live so tests can override settings)."""
    return int(settings.ollama_context_window * settings.summary_threshold_pct)


class MemoryContext(BaseModel):
    summary: str | None = None
    recent: list[dict] = Field(default_factory=list)  # [{role, content}], newest last
    truncated: bool = False

    def as_prompt(self) -> str:
        """Compact text form for injection into planner / agent prompts."""
        parts: list[str] = []
        if self.summary:
            parts.append(f"Conversation summary:\n{self.summary}")
        if self.recent:
            lines = "\n".join(f"{m['role']}: {m['content']}" for m in self.recent)
            parts.append(f"Recent conversation:\n{lines}")
        return "\n\n".join(parts)


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _format_turns(messages: list[dict]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)


def _summarize(
    provider: ModelProvider,
    text: str,
    cancel_event: threading.Event | None = None,
) -> str:
    # Native structured call (think=False is honored there — verified live:
    # no thinking field, direct answer): the summary is extraction work,
    # so chain-of-thought would only burn the 512-token budget. Any
    # failure (or a payload without the summary key, e.g. legacy test
    # doubles) falls back to the compat generate() path — same prompt,
    # same cap — so this never regresses.
    prompt = (
        "You maintain a rolling summary of a conversation. Read the previous "
        "summary and the new messages, then produce an updated concise summary "
        "in bullet points preserving key facts, decisions, and the current topic.\n\n"
        f"{text}"
        '\n\nReturn ONLY a JSON object like {"summary": "..."} '
        "with the bullet summary as the summary value."
    )
    try:
        result = provider.generate_structured(
            model=settings.ollama_default_model,
            messages=[{"role": "user", "content": prompt}],
            schema=_SUMMARY_SCHEMA,
            max_tokens=settings.summary_max_tokens,
            timeout_ms=settings.planner_timeout_ms,
            cancel_event=cancel_event,
        )
        summary = result.get("summary") if isinstance(result, dict) else None
        if isinstance(summary, str) and summary.strip():
            return summary
        logger.warning("summary structured call returned no summary, "
                       "falling back to generate()")
    except Exception as e:  # noqa: BLE001 - summary must never fail the run
        logger.warning("summary structured call failed (%s), "
                       "falling back to generate()", e)
    return provider.generate(
        model=settings.ollama_default_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=settings.summary_max_tokens,
        cancel_event=cancel_event,
    )


def build_memory_context(
    provider: ModelProvider,
    stored_summary: str | None,
    messages: list[dict],
    *,
    window_size: int = WINDOW_SIZE,
    max_tokens: int | None = None,
    folded_count: int = 0,
    cancel_event: threading.Event | None = None,
) -> tuple[MemoryContext, str | None, int]:
    """Build the bounded context and the (possibly updated) rolling summary.

    messages: [{role, content}] oldest-first. `folded_count` is how many of the
    older messages are already captured in `stored_summary` (persisted as
    notebooks.summary_message_count); only the turns that NEWLY aged out of
    the window get folded. Returns (context, new_summary, new_count); the
    caller persists new_summary → notebooks.conversation_summary.
    """
    budget = max_tokens if max_tokens is not None else _default_budget_tokens()
    recent_raw = messages[-window_size:]
    old = messages[:-window_size]
    new_summary = stored_summary
    new_count = folded_count

    if old and folded_count < len(old):
        newly = old[folded_count:]
        folded = _format_turns(newly)
        if new_summary:
            text = f"Previous summary:\n{new_summary}\n\nNew messages:\n{folded}"
        else:
            text = folded
        new_summary = _summarize(provider, text, cancel_event)
        new_count = len(old)
        logger.info(
            "rolling summary folded %d newly aged-out turn(s) (total %d in summary)",
            len(newly),
            new_count,
        )

    recent = [{"role": m["role"], "content": m["content"]} for m in recent_raw]

    # Token bound: keep the newest recent turns until the budget fits.
    truncated = False
    while (
        estimate_tokens(new_summary)
        + sum(estimate_tokens(t["content"]) for t in recent)
        > budget
        and len(recent) > 1
    ):
        recent = recent[1:]
        truncated = True
    if truncated:
        logger.warning("memory context token-bound trimmed recent turns")

    return (
        MemoryContext(summary=new_summary, recent=recent, truncated=truncated),
        new_summary,
        new_count,
    )
