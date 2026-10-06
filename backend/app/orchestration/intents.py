"""Intent taxonomy for layered planning.

L1 router classifies into these intents; L2 builders handle the
deterministic subset. Everything else (including UNKNOWN/low-confidence)
goes to L3 ReAct.

Pure data. NO LLM here.
"""

from __future__ import annotations

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
    UNKNOWN = "unknown"


#: Router confidence below this routes to UNKNOWN (→ L3 ReAct).
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
    Intent.CODE: "any software task: write new code from scratch in any language (including HTML/CSS/JS web pages, sites, apps, scripts), or explain, review, debug, test, or modify uploaded code files",
    Intent.UNKNOWN: "anything else or unclear",
}

#: Intents served by deterministic code-built DAGs (no DAG-LLM needed).
#: SUMMARIZE_PLOT is deliberately excluded: a plot needs content-derived
#: `labels` no fixed shape can know (inventing them would be the
#: hallucinated-chart class ADR-027 prevents), so it goes to L3 ReAct.
#: SUMMARIZE is included: per-file overview shards (mode=overview) fan
#: into one reduce step — wiring set by construction like compare_multi.
DETERMINISTIC_INTENTS = frozenset(
    {
        Intent.CHAT,
        Intent.QA_SINGLE,
        Intent.COMPARE_MULTI,
        Intent.SUMMARIZE,
        Intent.CONVERT_ONE,
        Intent.CONVERT_ALL,
        Intent.QUIZ,
        Intent.CODE,
    }
)
