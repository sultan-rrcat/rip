"""Intent taxonomy for layered planning.

L1 router classifies into these intents; L2 builders/specialists handle each.
L3 (existing mega-prompt) stays the fallback for UNKNOWN/low-confidence.

Pure data + tiny deterministic fast-path. NO LLM here.
"""

from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    CHAT = "chat"
    QA_SINGLE = "qa_single"
    COMPARE_MULTI = "compare_multi"
    SUMMARIZE = "summarize"
    SUMMARIZE_PLOT = "summarize_plot"
    PLOT_STANDALONE = "plot_standalone"
    REPORT = "report"
    CONVERT_ONE = "convert_one"
    CONVERT_ALL = "convert_all"
    CONVERT_AMBIGUOUS = "convert_ambiguous"
    QUIZ = "quiz"
    CODE = "code"
    IMAGE = "image"
    VISION = "vision"
    UNKNOWN = "unknown"


#: Router confidence below this routes to UNKNOWN (→ L3 mega-prompt → ReAct).
ROUTER_CONFIDENCE_THRESHOLD = 0.6

#: One line per intent for the tiny router prompt (kept here so prompts stay small).
INTENT_DESCRIPTIONS: dict[Intent, str] = {
    Intent.CHAT: "greeting, thanks, or small talk with no document question",
    Intent.QA_SINGLE: "one factual question answered from documents",
    Intent.COMPARE_MULTI: "compare, contrast, or rank two or more documents/topics (no chart requested)",
    Intent.SUMMARIZE: "summarize documents without chart or report file",
    Intent.SUMMARIZE_PLOT: "summarize/compare AND draw/plot/chart the numbers — any plot/draw/chart/show-as-graph ask belongs here, even when the request also says compare",
    Intent.PLOT_STANDALONE: "draw a chart from numbers given in the message",
    Intent.REPORT: "write the answer as a titled report file (doc.generate)",
    Intent.CONVERT_ONE: "convert one named file to md/docx/pdf",
    Intent.CONVERT_ALL: "convert all/plural documents to md/docx/pdf",
    Intent.CONVERT_AMBIGUOUS: "convert it/the document without naming file or format",
    Intent.QUIZ: "generate questions, quiz, or MCQs from documents",
    Intent.CODE: "write, explain, review, or debug code",
    Intent.IMAGE: "generate or draw an image",
    Intent.VISION: "analyze an image, diagram, or visual content",
    Intent.UNKNOWN: "anything else or unclear",
}

#: Intents served by deterministic code-built DAGs (no DAG-LLM needed).
DETERMINISTIC_INTENTS = frozenset(
    {
        Intent.CHAT,
        Intent.QA_SINGLE,
        Intent.COMPARE_MULTI,
        Intent.SUMMARIZE_PLOT,
        Intent.CONVERT_ONE,
        Intent.CONVERT_ALL,
        Intent.QUIZ,
    }
)

_GREETING_RE = re.compile(
    r"^\s*(hi+|hii+|hello|hey|yo|thanks|thank you|bye|good\s?(morning|afternoon|evening))\b",
    re.IGNORECASE,
)


def classify_fast_path(text: str | None) -> Intent | None:
    """L0 deterministic fast-path: greetings/empty → CHAT, else None (needs router)."""
    if text is None or not text.strip():
        return Intent.CHAT
    stripped = text.strip()
    # Greeting-led short messages only — "hey compare both reports" must NOT match.
    if len(stripped) <= 24 and _GREETING_RE.match(stripped):
        return Intent.CHAT
    return None
