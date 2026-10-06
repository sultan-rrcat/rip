"""L1 intent router — one tiny structured LLM call, the sole dispatcher.

Classifies the user request into exactly one intent plus slot values
(file_hint, target_format). Returns Intent + confidence; callers
route < threshold to UNKNOWN (→ L3 ReAct). Every request — including
greetings — goes through the LLM; there is no deterministic fast-path.
Query generation is performed inside rag.query, not by the router.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

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
        "confidence": {"type": "number"},
        "file_hint": {"type": "string"},
        "target_format": {"type": "string"},
    },
    "required": ["intent", "confidence"],
}

#: Formats doc.convert accepts; anything else means "format unstated".
_CONVERT_FORMATS = frozenset({"md", "docx", "pdf"})


class RouterResult(BaseModel):
    intent: Intent = Intent.UNKNOWN
    confidence: float = 0.0
    routed_by: str = "llm"
    # Convert slots: file_hint names one file (or "*" for all) and
    # target_format is md|docx|pdf. Empty = unstated → caller falls
    # through to L3 ReAct (which asks the counter-question) instead of
    # guessing a conversion. For CODE, file_hint names one uploaded
    # code file only when the request names one (else "" — which also
    # covers greenfield generation with no files attached).
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

    def route(self, request_text: str, context: str | None = None) -> RouterResult:
        lines = "\n".join(
            f"- {intent.value}: {INTENT_DESCRIPTIONS[intent]}" for intent in Intent
        )
        system_prompt = (
            "You are an intent router. Classify the user request into exactly "
            "one intent. Query generation for document retrieval is performed "
            "inside rag.query, not by you.\n"
            f"Intents:\n{lines}\n"
            "Precedence: plot/draw/chart/show-as-graph (from document data) "
            "is always summarize_plot, even when the request also says "
            "compare; compare_multi is only for comparisons with no chart.\n"
            "Convert intents only: file_hint is the named file (or \"*\" when "
            "the request says all/every documents, else \"\"), target_format "
            "is md|docx|pdf when stated (else \"\").\n"
            "Code intent covers ALL software asks, with or without uploaded "
            "files: web page/website/landing page/HTML/CSS/JS/UI/app/script, "
            "and write/explain/review/debug/test/refactor code in any "
            "language. Requests to generate or draw pictures, photos, or "
            "illustrations are NOT code — they are unknown. "
            "Code file_hint is the named uploaded code file only when the "
            "request names one (else \"\" for all code files or for "
            "greenfield generation with no files).\n"
            "Return intent as the exact value string and confidence as 0.0-1.0."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request_text},
        ]
        if context:
            # Follow-up fragments ("in a table format", "now as bullets") are
            # unclassifiable alone (trace 35e8fbd9: bare fragment -> unknown).
            # Recent turns let the router classify the combined intent.
            messages.insert(
                1,
                {"role": "system", "content": (
                    "Conversation context (recent turns, oldest first). The "
                    "request may be a follow-up to it — classify the combined "
                    "intent:\n" + context
                )},
            )
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
        return RouterResult(
            intent=intent,
            confidence=confidence,
            file_hint=file_hint,
            target_format=target_format,
        )
