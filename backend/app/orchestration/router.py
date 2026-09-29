"""L1 intent router — one tiny structured LLM call, the sole dispatcher.

Classifies the user request into exactly one intent plus slot values
(queries, file_hint, target_format). Returns Intent + confidence; callers
route < threshold to UNKNOWN (→ L3 ReAct). Every request — including
greetings — goes through the LLM; there is no deterministic fast-path.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.core.config import settings
from app.orchestration.intents import (
    INTENT_DESCRIPTIONS,
    ROUTER_CONFIDENCE_THRESHOLD,
    Intent,
)
from app.providers.base import ModelProvider

logger = logging.getLogger("orchestration.router")

ROUTER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "file_hint": {"type": "string"},
        "target_format": {"type": "string"},
    },
    "required": ["intent", "confidence"],
}

#: Formats doc.convert accepts; anything else means "format unstated".
_CONVERT_FORMATS = frozenset({"md", "docx", "pdf"})

#: Intents grounded in notebook documents: the router must always supply
#: at least one search query for these (post-filled from the request text
#: when the model returns an empty list — trace ea48cb30 returned
#: qa_single with queries=[] and masked the miss downstream).
_DOC_GROUNDED_INTENTS = frozenset({
    Intent.QA_SINGLE,
    Intent.COMPARE_MULTI,
    Intent.SUMMARIZE,
    Intent.SUMMARIZE_PLOT,
    Intent.REPORT,
    Intent.QUIZ,
})


class RouterResult(BaseModel):
    intent: Intent = Intent.UNKNOWN
    queries: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    routed_by: str = "llm"
    # Convert slots: file_hint names one file (or "*" for all) and
    # target_format is md|docx|pdf. Empty = unstated → caller falls
    # through to L3 ReAct (which asks the counter-question) instead of
    # guessing a conversion.
    file_hint: str = ""
    target_format: str = ""


class Router:
    def __init__(
        self,
        provider: ModelProvider,
        model: str | None = None,
    ):
        self._provider = provider
        self._model = model or settings.ollama_default_model

    def route(self, request_text: str) -> RouterResult:
        lines = "\n".join(
            f"- {intent.value}: {INTENT_DESCRIPTIONS[intent]}" for intent in Intent
        )
        system_prompt = (
            "You are an intent router. Classify the user request into exactly "
            "one intent and propose 1-3 short document search queries "
            "(empty list when the intent needs no documents).\n"
            f"Intents:\n{lines}\n"
            "Document-grounded intents (qa_single, compare_multi, summarize, "
            "summarize_plot, report, quiz) MUST return 1-3 queries — never "
            "an empty list. Non-document intents (chat, convert_*, code, "
            "image, vision, plot_standalone, unknown) return [].\n"
            "Precedence: plot/draw/chart/show-as-graph (from document data) "
            "is always summarize_plot, even when the request also says "
            "compare; compare_multi is only for comparisons with no chart. "
            "Make the queries distinct from each other (one angle per query).\n"
            "Convert intents only: file_hint is the named file (or \"*\" when "
            "the request says all/every documents, else \"\"), target_format "
            "is md|docx|pdf when stated (else \"\").\n"
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
        except Exception as e:  # noqa: BLE001 - fail-open to L3 ReAct
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
        file_hint = raw.get("file_hint", "") or ""
        file_hint = file_hint.strip() if isinstance(file_hint, str) else ""
        target_format = raw.get("target_format", "") or ""
        target_format = (
            target_format.strip().lower() if isinstance(target_format, str) else ""
        )
        if target_format not in _CONVERT_FORMATS:
            target_format = ""
        if confidence < ROUTER_CONFIDENCE_THRESHOLD:
            intent = Intent.UNKNOWN
        if intent in _DOC_GROUNDED_INTENTS and not queries:
            queries = [request_text.strip()][:1]
            logger.info(
                "router post-filled empty queries for intent=%s", intent.value
            )
        return RouterResult(
            intent=intent,
            queries=queries,
            confidence=confidence,
            file_hint=file_hint,
            target_format=target_format,
        )
