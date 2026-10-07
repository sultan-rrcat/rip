"""Intent taxonomy for layered planning.

L1 router classifies into these intents; L2 builders handle the
deterministic subset. Everything else (including UNKNOWN) goes to L3 ReAct.

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
    CONVERT_ONE = "convert_one"
    CONVERT_ALL = "convert_all"
    QUIZ = "quiz"
    CODE = "code"
    UNKNOWN = "unknown"


#: Deprecated: confidence was removed from the router contract (the LLM now
#: returns intent only; parse success is trust). Kept for import
#: compatibility; no routing code reads it.
ROUTER_CONFIDENCE_THRESHOLD = 0.6

#: One line per intent for the tiny router prompt (kept here so prompts stay small).
INTENT_DESCRIPTIONS: dict[Intent, str] = {
    Intent.CHAT: "greeting, thanks, or small talk with no document question",
    Intent.QA_SINGLE: "one factual question answered from documents",
    Intent.COMPARE_MULTI: "compare, contrast, or rank two or more documents/topics (no chart requested)",
    Intent.SUMMARIZE: "summarize documents without chart",
    Intent.SUMMARIZE_PLOT: "summarize/compare AND draw/plot/chart the numbers — any plot/draw/chart/show-as-graph ask belongs here, even when the request also says compare",
    Intent.PLOT_STANDALONE: "draw a chart from numbers given in the message",
    Intent.CONVERT_ONE: "re-render one ORIGINAL uploaded file to md/docx/pdf via convert/export/save-as (requires a named source file; write/create/generate/draft new content such as an email/letter/report in pdf is NOT convert)",
    Intent.CONVERT_ALL: "re-render all ORIGINAL uploaded files to md/docx/pdf via convert/export/save-as (requires all/every/each; new content is NOT convert)",
    Intent.QUIZ: "generate questions, quiz, or MCQs from documents",
    Intent.CODE: "any software task: write new code from scratch in any language (including HTML/CSS/JS web pages, sites, apps, scripts), or explain, review, debug, test, or modify uploaded code files",
    Intent.UNKNOWN: "anything else or unclear",
}

#: Intents served by deterministic code-built DAGs (no DAG-LLM needed).
#: SUMMARIZE is included: per-file overview shards (mode=overview) fan
#: into one reduce step — wiring set by construction like compare_multi.
#: PLOT_STANDALONE is included: labels+values are parsed from the message
#: text in Python, so nothing is invented (standalone literals stay legal).
#: SUMMARIZE_PLOT is deliberately excluded: chart labels are content-derived
#: and their COUNT is independent of the file count. Trace cb0e6ab0 (1 file,
#: 6 benchmark rows) proved a file-stem label set cannot work — it produced a
#: 1-label/6-value plot rejected as "'labels' and 'values' must have the same
#: length", and any length-matching shape would mislabel each row as a file.
#: REPORT/CONVERT_AMBIGUOUS were removed: report asks route via
#: SUMMARIZE/QA_SINGLE (doc.generate stays ReAct-reachable) and ambiguous
#: converts fall to UNKNOWN → ReAct, which asks the counter-question. Stale
#: "report"/"convert_ambiguous" router values fail closed to UNKNOWN.
DETERMINISTIC_INTENTS = frozenset(
    {
        Intent.CHAT,
        Intent.QA_SINGLE,
        Intent.COMPARE_MULTI,
        Intent.SUMMARIZE,
        Intent.PLOT_STANDALONE,
        Intent.CONVERT_ONE,
        Intent.CONVERT_ALL,
        Intent.QUIZ,
        Intent.CODE,
    }
)
