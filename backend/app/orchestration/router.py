"""L1 intent router — rule-based pre-filter + one tiny structured LLM call.

Classifies the user request into exactly one intent. Slots (file_hint,
target_format) are extracted deterministically in Python by regex — never
by the LLM. There is no confidence score: the LLM returns intent only,
and every parsed intent is trusted (fail-open to UNKNOWN only on
transport/parse errors, which route to L3 ReAct).

Order of operations in Router.route():
  1. Cautious rule pre-filter (_rule_pre_filter): only high-precision
     patterns (pure greetings, explicit convert + format, numbers-in-message
     chart, self-contained-HTML code marker, explicit quiz ask). On hit,
     no LLM call is spent (routed_by="rule").
  2. Otherwise one LLM call with the deliverable-based prompt
     (_build_system_prompt): what the user wants back decides, with
     explicit tie-break priorities for overlapping patterns; no anecdotes,
     no trace IDs. Output schema is intent-only. A short corpus hint
     (empty/processing/ready, never full chunk text) may be appended so
     the router does not invent files it cannot see.
  3. Slots filled by _extract_target_format/_extract_file_hint over the raw
     request text, then validated downstream (builders resolve file_hint
     against the notebook snapshot; unknown names fall to ReAct).

Query generation is performed inside rag.query, not by the router.
"""

from __future__ import annotations

import logging
import re
import threading

from pydantic import BaseModel

from app.core.config import settings
from app.orchestration.intents import INTENT_DESCRIPTIONS, Intent
from app.providers.base import ModelProvider

logger = logging.getLogger("orchestration.router")

ROUTER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [intent.value for intent in Intent],
        },
    },
    "required": ["intent"],
}

#: Output cap for the router call: one enum word of JSON. With thinking
#: models the chain-of-thought burns the same num_predict budget as the
#: answer, so the shared 2048 default would let a think-burn ride the
#: full 20s deadline for ~10 tokens of output.
ROUTER_MAX_TOKENS = 256

#: Formats doc.convert accepts; anything else means "format unstated".
_CONVERT_FORMATS = frozenset({"md", "docx", "pdf"})

_FORMAT_RE = re.compile(r"(?<!\.)\b(md|markdown|docx|pdf)\b", re.IGNORECASE)
_ALL_DOCS_RE = re.compile(r"\b(all|every|each)\b[^.]{0,40}\b(documents?|files?)\b", re.IGNORECASE)
_CONVERT_RE = re.compile(r"\b(convert|export|save\s+as)\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r"""["']([^"']+\.\w+)["']""")
_FILENAME_RE = re.compile(r"\b([\w][\w\-.]*\.(?:pdf|docx|md))\b", re.IGNORECASE)
_CODE_FILENAME_RE = re.compile(
    r"\b([\w][\w\-.]*\.(?:py|js|ts|tsx|jsx|html|css|java|go|rs|cpp|c|h))\b",
    re.IGNORECASE,
)
_CHAT_RE = re.compile(
    r"^(hi|hello|hey|hi there|hello there|thanks|thank you|"
    r"good morning|good afternoon|good evening|bye|goodbye)[.!]*$",
    re.IGNORECASE,
)
_QUIZ_RE = re.compile(r"\b(quiz|mcqs?|multiple[ -]choice|flashcards)\b", re.IGNORECASE)
_QUIZ_VERB_RE = re.compile(
    r"\b(generate|create|make|write|give|produce)\b", re.IGNORECASE
)
_CODE_EXPLICIT_RE = re.compile(
    r"(self-contained\s+(html|page|website)"
    r"|return\s+only.*html"
    r"|no\s+cdn"
    r"|<!DOCTYPE\s+html)",
    re.IGNORECASE,
)
_CHART_WORD_RE = re.compile(
    r"\b(plot|chart|graph|bar\s+chart|line\s+chart|visualiz\w*|draw)\b",
    re.IGNORECASE,
)
_LABEL_VALUE_RE = re.compile(r"[A-Za-z][\w ]*\s*:\s*\d+(?:\.\d+)?")


def _extract_target_format(request_text: str) -> str:
    """Deterministic format slot: md|docx|pdf or "" when unstated."""
    match = _FORMAT_RE.search(request_text or "")
    if not match:
        return ""
    token = match.group(1).lower()
    if token == "markdown":
        return "md"
    return token if token in _CONVERT_FORMATS else ""


def _extract_file_hint(request_text: str, intent: Intent) -> str:
    """Deterministic file slot from raw text (never LLM-generated).

    Convert intents: "*" when the request says all/every/each documents,
    else a quoted filename or bare `name.ext`, else "".
    Code intent: a bare code filename when named, else "" (greenfield).
    All other intents: "".
    """
    text = request_text or ""
    if intent in (Intent.CONVERT_ALL, Intent.CONVERT_ONE):
        if _ALL_DOCS_RE.search(text):
            return "*"
        quoted = _QUOTED_RE.search(text)
        if quoted:
            return quoted.group(1).strip()
        bare = _FILENAME_RE.search(text)
        if bare:
            return bare.group(1).strip()
        return ""
    if intent is Intent.CODE:
        quoted = _QUOTED_RE.search(text)
        if quoted:
            return quoted.group(1).strip()
        bare = _CODE_FILENAME_RE.search(text)
        if bare:
            return bare.group(1).strip()
        return ""
    return ""


def _rule_pre_filter(request_text: str) -> Intent | None:
    """Cautious high-precision pre-filter; None = ask the LLM.

    Only fires when the text carries an unambiguous signal. Everything
    ambiguous (compare vs summarize_plot, qa vs summarize, code without
    explicit markers) returns None so the LLM decides.
    """
    text = (request_text or "").strip()
    if not text:
        return None
    lowered = text.lower()

    # 1. Pure greeting / small talk, nothing else.
    if len(text) < 40 and _CHAT_RE.match(text):
        return Intent.CHAT

    has_convert = bool(_CONVERT_RE.search(text))
    fmt = _extract_target_format(text)
    if has_convert and fmt:
        if _ALL_DOCS_RE.search(text):
            return Intent.CONVERT_ALL
        if _QUOTED_RE.search(text) or _FILENAME_RE.search(text):
            return Intent.CONVERT_ONE
        # "convert all ..." without the word documents, or bare
        # "convert to pdf" — still unambiguous enough for a rule hit;
        # builders/ReAct resolve ambiguity downstream.
        if re.search(r"\ball\b", lowered):
            return Intent.CONVERT_ALL
        return Intent.CONVERT_ONE

    # 2. Explicit quiz ask.
    if _QUIZ_RE.search(text) and _QUIZ_VERB_RE.search(text):
        return Intent.QUIZ

    # 3. Explicit self-contained-HTML code marker (the old c1bbae95 class).
    if _CODE_EXPLICIT_RE.search(text):
        return Intent.CODE

    # 4. Standalone chart: chart verb + ≥2 literal label:number pairs.
    if _CHART_WORD_RE.search(text):
        pairs = _LABEL_VALUE_RE.findall(text)
        if len(pairs) >= 2:
            return Intent.PLOT_STANDALONE

    return None


def _corpus_hint_for_router(notebook_context: str | None) -> str | None:
    """Tiny corpus hint so the router does not invent unseen files.

    Returns a short state summary (empty/processing/ready/code-only) or
    None when the snapshot is missing (DB failure — database is ground
    truth, so the router classifies blind as before). Names are included
    truncated: they disambiguate convert_one ("that PDF") from convert_all
    and, critically, expose code extensions (.py/.js/...) so a
    "read all the files" ask over code files routes to CODE, not
    summarize — without pasting chunk text into the routing call.

    Code-only notebooks report "empty" from get_corpus_state (no
    embeddings to retrieve), but must NOT render as "no documents
    uploaded": the files exist, they are just code.
    """
    if notebook_context is None:
        return None
    try:
        from app.orchestration.corpus import (
            _ready_code,
            _ready_files,
            _snapshot_files,
            get_corpus_state,
        )
    except Exception:  # noqa: BLE001 - hint is best-effort, never fatal
        return None
    try:
        state = get_corpus_state(notebook_context)
        code = _ready_code(notebook_context)
    except Exception:  # noqa: BLE001 - malformed snapshot classifies blind
        return None
    code_names = ", ".join(n for n, _ in code[:5])[:200]
    code_suffix = (
        f" Plus {len(code)} ready code file(s)."
        + (f" Code names: {code_names}." if code_names else "")
        if code
        else ""
    )
    if state == "empty":
        if code:
            return (
                f"Notebook file state: CODE-ONLY — no searchable documents, "
                f"but {len(code)} ready code file(s) present."
                + (f" Names: {code_names}." if code_names else "")
                + " A request to read/review/explain/debug/identify issues "
                "in THOSE files is a CODE task, not summarize."
            )
        return "Notebook file state: EMPTY — no documents uploaded yet."
    if state == "processing":
        return (
            "Notebook file state: PROCESSING — files exist but none is "
            "ready yet (still uploading/processing/errored)."
            + code_suffix
        )
    if state == "ready":
        try:
            ready = _ready_files(notebook_context)
            total = len(_snapshot_files(notebook_context))
        except Exception:  # noqa: BLE001 - counts are cosmetic
            ready, total = [], 0
        names = ", ".join(n for n, _ in ready[:5])[:200]
        extra = f", total files: {total}" if total != len(ready) else ""
        return (
            f"Notebook file state: READY — {len(ready)} ready "
            f"document(s){extra}."
            + (f" Names: {names}." if names else "")
            + code_suffix
        )
    return None


#: Admin-editable router prompt template (see app/core/promptstore.py).
#: Placeholders (required — PUT refuses bodies missing them):
#:   {intents_block}  — rendered from the Intent enum + INTENT_DESCRIPTIONS
#:   {corpus_section} — "" when no hint, else the corpus suffix below
ROUTER_PROMPT_TEMPLATE = (
    "You are an intent router. Output EXACTLY one JSON object, no prose. "
    "Query generation for document retrieval is performed inside rag.query, "
    "not by you.\n"
    "Intents:\n{intents_block}\n"
    "Decide by DELIVERABLE — what the user wants back:\n"
    "1. code: deliverable is SOURCE CODE or program text. Requires an "
    "explicit software signal: a programming language, code file (e.g. "
    ".py/.js/.ts), traceback/error, function/class/test/refactor, or "
    "greenfield build (script/app/page/site/component). The notebook "
    "file list itself counts as the signal: when it names ready code "
    "files and the request asks to read/review/explain/debug those "
    "files, that IS code. Review/explain/"
    "summarize/critique of DOCUMENT content (plan/report/checklist/"
    "architecture/findings, snippet on page N) is NOT code — it is "
    "summarize/qa_single. Chart/plot/table/compare words do NOT override "
    "this when code is requested. Pictures/photos/illustrations with no "
    "code requested are unknown.\n"
    "2. convert_all: deliverable is a reformatted ORIGINAL uploaded file set "
    "and the request says all/every/each documents via convert/export/"
    "save-as. Write/create/generate/draft NEW content (email/letter/"
    "report/essay in pdf/docx/md) is NOT convert — it is unknown, even "
    "when it names pdf/docx/md.\n"
    "3. convert_one: same, but one named ORIGINAL file. Requires a "
    "convert/export/save-as verb AND a source file; without both, use "
    "unknown.\n"
    "4. plot_standalone: deliverable is a chart AND every label and number "
    "needed is IN the message (for example 'A: 10, B: 20').\n"
    "5. summarize_plot: deliverable is a chart AND the numbers must come "
    "FROM documents (plot/draw/chart/graph/show-as-graph over document "
    "data, with or without compare words).\n"
    "6. compare_multi: text comparing/contrasting/ranking 2+ docs/topics, "
    "no chart.\n"
    "7. summarize: text summary/abstract/overview/review/critique of docs "
    "(e.g. 'review the plan in the doc'), no chart, no "
    "questions.\n"
    "8. quiz: questions/quiz/MCQs/flashcards from docs.\n"
    "9. qa_single: one factual question answered from docs.\n"
    "10. chat: pure greeting/thanks/small-talk/farewell ONLY, no document "
    "question, no task. A greeting plus any task routes to the task, "
    "never chat (e.g. 'hello, summarize this doc' is summarize).\n"
    "11. unknown: anything else or ambiguous.\n"
    "Tie-breaks (only when two patterns genuinely overlap): prefer the "
    "more specific deliverable — code over chart over compare/quiz over "
    "qa_single/summarize over chat. A chart verb plus document data "
    "is summarize_plot even when the request also says compare; a chart "
    "verb plus literal numbers in the message is plot_standalone; "
    "review/explain of document prose is summarize/qa_single, never code; "
    "review/explain of notebook code files is code, never summarize.\n"
    "Follow-ups: a formatting-only fragment (table/bullets) inherits the "
    "prior intent unless it adds a new chart/convert/code/quiz verb. "
    "Always classify the COMBINED intent.\n"
    'Return {"intent": "<one of the values above>"}.{corpus_section}'
)

#: Corpus-hint suffix spliced into {corpus_section} when a hint exists.
#: Required placeholder: {corpus_hint}.
ROUTER_CORPUS_SUFFIX_TEMPLATE = (
    "\nNotebook context (do NOT change the deliverable because of "
    "it; use it only to avoid inventing files): "
    "{corpus_hint}"
    " Never invent a file name or id — convert intents still "
    "require the verb plus the file named in the request text."
)


def _build_system_prompt(corpus_hint: str | None = None) -> str:
    from app.core.promptstore import get_prompt

    lines = "\n".join(
        f"- {intent.value}: {INTENT_DESCRIPTIONS[intent]}" for intent in Intent
    )
    # Plain .replace (never .format): prompt prose carries literal JSON
    # braces that .format would try to interpolate. Intent block first so
    # a hint containing a token stays literal.
    template = get_prompt("router.system_prompt")
    section = ""
    if corpus_hint:
        section = get_prompt("router.corpus_suffix").replace(
            "{corpus_hint}", corpus_hint
        )
    return template.replace("{intents_block}", lines).replace(
        "{corpus_section}", section
    )


class RouterResult(BaseModel):
    intent: Intent = Intent.UNKNOWN
    # Deprecated: kept for wire/test compatibility, always 1.0 on a parsed
    # intent and 0.0 on UNKNOWN. The LLM no longer emits it and no threshold
    # gates routing — parse success is trust.
    confidence: float = 1.0
    # "rule" = cautious pre-filter hit (no LLM call), "llm" = classified by
    # the LLM, "none" = engine default before routing runs.
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

    def route(
        self,
        request_text: str,
        context: str | None = None,
        cancel_event: threading.Event | None = None,
        notebook_context: str | None = None,
    ) -> RouterResult:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("run cancelled")
        # Fast path: cautious deterministic rules, no LLM call.
        try:
            rule_intent = _rule_pre_filter(request_text)
        except Exception:  # noqa: BLE001 - regex must never break routing
            rule_intent = None
        if rule_intent is not None:
            return RouterResult(
                intent=rule_intent,
                confidence=1.0,
                routed_by="rule",
                file_hint=_extract_file_hint(request_text, rule_intent),
                target_format=(
                    _extract_target_format(request_text)
                    if rule_intent
                    in (Intent.CONVERT_ONE, Intent.CONVERT_ALL)
                    else ""
                ),
            )

        system_prompt = _build_system_prompt(_corpus_hint_for_router(notebook_context))
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request_text},
        ]
        if context:
            # Follow-up fragments ("in a table format", "now as bullets") are
            # unclassifiable alone — recent turns let the router classify the
            # combined intent.
            messages.insert(
                1,
                {"role": "system", "content": (
                    "Conversation context (recent turns, oldest first). The "
                    "request may be a follow-up to it — classify the COMBINED "
                    "intent. A formatting-only fragment (table/bullets) "
                    "inherits the prior intent unless it adds a new "
                    "chart/convert/code/quiz verb. A greeting plus any task "
                    "routes to the task, never chat.\n" + context
                )},
            )
        try:
            raw = self._provider.generate_structured(
                model=self._model,
                messages=messages,
                schema=ROUTER_SCHEMA,
                temperature=0,
                # Tight deadline: this call emits ~10 tokens of JSON. Under
                # Ollama saturation the inherited generation budget (300s)
                # held the run hostage — a saturated server once spent the
                # entire run timeout on router + planner timeouts and did no
                # work. Failing open to UNKNOWN fast leaves budget for ReAct.
                timeout_ms=settings.router_timeout_ms,
                cancel_event=cancel_event,
                # Tiny output (one enum word): cap tightly so a think-burn
                # fails fast instead of riding the deadline (see
                # ROUTER_MAX_TOKENS). Native endpoint honors think=False,
                # so this is belt-and-braces.
                max_tokens=ROUTER_MAX_TOKENS,
            )
        except Exception as e:  # noqa: BLE001 - fail-open to L3 ReAct
            logger.warning("router LLM failed, falling back to unknown: %s", e)
            return RouterResult(
                intent=Intent.UNKNOWN, confidence=0.0, routed_by="llm"
            )
        try:
            intent = Intent(str(raw.get("intent", "unknown")).strip().lower())
        except ValueError:
            intent = Intent.UNKNOWN
        file_hint = _extract_file_hint(request_text, intent)
        target_format = (
            _extract_target_format(request_text)
            if intent in (Intent.CONVERT_ONE, Intent.CONVERT_ALL)
            else ""
        )
        if intent in (Intent.CONVERT_ONE, Intent.CONVERT_ALL) and (
            not file_hint or not target_format
        ):
            # Slot-guard: a convert without a resolvable source and an
            # explicit md/docx/pdf target is a model hallucination (e.g.
            # "write an email in pdf format" — new content, no file).
            # Fail open to UNKNOWN so ReAct drafts + doc.generate
            # instead of a builder miss with convert-biased plan_error.
            return RouterResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                routed_by="llm",
                file_hint=file_hint,
                target_format=target_format,
            )
        if intent is Intent.UNKNOWN:
            return RouterResult(
                intent=intent,
                confidence=0.0,
                routed_by="llm",
                file_hint=file_hint,
                target_format=target_format,
            )
        return RouterResult(
            intent=intent,
            confidence=1.0,
            routed_by="llm",
            file_hint=file_hint,
            target_format=target_format,
        )
