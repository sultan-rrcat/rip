"""L1 intent router — one tiny structured LLM call (or no call on fast-path).

Replaces the mega-prompt's implicit intent classification with an explicit,
cheap step. Returns Intent + search-query slots + confidence; callers route
< threshold to UNKNOWN (→ L3 mega-prompt → ReAct).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.core.config import settings
from app.orchestration.intents import (
    INTENT_DESCRIPTIONS,
    ROUTER_CONFIDENCE_THRESHOLD,
    Intent,
    classify_fast_path,
)
from app.providers.base import ModelProvider

logger = logging.getLogger("orchestration.router")

ROUTER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["intent", "confidence"],
}


class RouterResult(BaseModel):
    intent: Intent = Intent.UNKNOWN
    queries: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    routed_by: str = "llm"  # "fast_path" when classify_fast_path hit


class Router:
    def __init__(
        self,
        provider: ModelProvider,
        model: str | None = None,
    ):
        self._provider = provider
        self._model = model or settings.ollama_default_model

    def route(self, request_text: str) -> RouterResult:
        fast = classify_fast_path(request_text)
        if fast is not None:
            return RouterResult(
                intent=fast, queries=[], confidence=1.0, routed_by="fast_path"
            )
        lines = "\n".join(
            f"- {intent.value}: {INTENT_DESCRIPTIONS[intent]}" for intent in Intent
        )
        system_prompt = (
            "You are an intent router. Classify the user request into exactly "
            "one intent and propose 1-3 short document search queries "
            "(empty list when the intent needs no documents).\n"
            f"Intents:\n{lines}\n"
            "Precedence: plot/draw/chart/show-as-graph (from document data) "
            "is always summarize_plot, even when the request also says "
            "compare; compare_multi is only for comparisons with no chart. "
            "Make the queries distinct from each other (one angle per query).\n"
            "Return intent as the exact value string, queries as a list, "
            "confidence as 0.0-1.0."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request_text},
        ]
        try:
            raw = self._provider.generate_structured(
                model=self._model,
                messages=messages,
                schema=ROUTER_SCHEMA,
                temperature=0,
            )
        except Exception as e:  # noqa: BLE001 - fail-open to L3 fallback
            logger.warning("router LLM failed, falling back to unknown: %s", e)
            return RouterResult(intent=Intent.UNKNOWN, routed_by="llm")
        try:
            intent = Intent(str(raw.get("intent", "unknown")).strip().lower())
        except ValueError:
            intent = Intent.UNKNOWN
        queries = raw.get("queries", []) or []
        queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()][
            :3
        ]
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))
        if confidence < ROUTER_CONFIDENCE_THRESHOLD:
            intent = Intent.UNKNOWN
        return RouterResult(intent=intent, queries=queries, confidence=confidence)
