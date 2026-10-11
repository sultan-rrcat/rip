"""quiz.generate tool — validate + normalize structured quiz JSON.

The LLM never renders interactive HTML. One reasoning step emits a strict
{"questions": [...]} object; this tool validates it deterministically
(same fail-honest philosophy as plot.chart `data`) and normalizes it into
`quiz_json` for the frontend `QuizArtifact` renderer + file artifact.

Effect class: sandboxed (bounded compute producing an artifact).
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from app.tools.base import Tool, ToolRequest, ToolResponse

#: Bounds mirror plot.chart's _MAX_POINTS philosophy: enough for a real
#: test, small enough to survive the Ollama window + chat rendering.
QUIZ_MIN_QUESTIONS = 2
QUIZ_MAX_QUESTIONS = 50
QUIZ_MIN_OPTIONS = 2
QUIZ_MAX_OPTIONS = 6
QUIZ_MAX_QUESTIONS_DEFAULT = 10

VALID_QTYPES = ("mcq", "msq", "truefalse")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    return t


def parse_quiz_json(data: object) -> dict | None:
    """Parse a quiz payload from upstream step text or a decoded dict.

    Accepts a raw JSON string (optionally fence-wrapped or padded with one
    sentence of prose — first balanced {...} wins) or an already-decoded
    dict. Must be {"questions": [...]} (a bare list is also accepted).
    Returns None on any shape mismatch.
    """
    if isinstance(data, dict):
        obj: object = data
    elif isinstance(data, str):
        text = _strip_fences(data)
        try:
            obj = json.loads(text)
        except (ValueError, UnicodeDecodeError):
            start = text.find("{")
            if start < 0:
                # Bare list fallback: [ {...}, ... ]
                lstart = text.find("[")
                if lstart < 0:
                    return None
                try:
                    arr, _ = json.JSONDecoder().raw_decode(text[lstart:])
                except ValueError:
                    return None
                obj = {"questions": arr}
            else:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(text[start:])
                except ValueError:
                    return None
    else:
        return None
    if isinstance(obj, list):
        obj = {"questions": obj}
    if not isinstance(obj, dict):
        return None
    questions = obj.get("questions")
    if not isinstance(questions, list):
        return None
    return {"questions": questions, "title": obj.get("title", "")}


def validate_quiz(payload: dict) -> tuple[list[dict] | None, str | None]:
    """Validate normalized payload. Returns (questions, error)."""
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        return None, "'questions' must be a non-empty array"
    if len(questions) < QUIZ_MIN_QUESTIONS:
        return None, f"at least {QUIZ_MIN_QUESTIONS} questions required"
    if len(questions) > QUIZ_MAX_QUESTIONS:
        return None, f"at most {QUIZ_MAX_QUESTIONS} questions per quiz"
    clean: list[dict] = []
    for i, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            return None, f"question {i} must be an object"
        qtype = str(q.get("type", "mcq")).strip().lower()
        if qtype not in VALID_QTYPES:
            return None, f"question {i} type must be one of {', '.join(VALID_QTYPES)}"
        stem = q.get("stem", q.get("question", ""))
        if not isinstance(stem, str) or not stem.strip():
            return None, f"question {i} needs a non-empty 'stem'"
        options = q.get("options")
        if not isinstance(options, list) or not (QUIZ_MIN_OPTIONS <= len(options) <= QUIZ_MAX_OPTIONS):
            return None, (
                f"question {i} needs {QUIZ_MIN_OPTIONS}-{QUIZ_MAX_OPTIONS} options"
            )
        opt_ids: list[str] = []
        opt_out: list[dict] = []
        for j, o in enumerate(options):
            if isinstance(o, str):
                oid, text = f"o{j + 1}", o
            elif isinstance(o, dict):
                oid = str(o.get("id", f"o{j + 1}"))
                text = o.get("text", o.get("label", ""))
                if not isinstance(text, str) or not text.strip():
                    return None, f"question {i} option {j + 1} needs non-empty text"
            else:
                return None, f"question {i} option {j + 1} must be string or {{id, text}}"
            if oid in opt_ids:
                return None, f"question {i} has duplicate option id {oid!r}"
            opt_ids.append(oid)
            opt_out.append({"id": oid, "text": text.strip() if isinstance(text, str) else text})
        answers = q.get("answers", q.get("answer", q.get("correct", [])))
        if isinstance(answers, str):
            answers = [answers]
        if not isinstance(answers, list) or not answers:
            return None, f"question {i} needs a non-empty 'answers' array of option ids"
        ans_ids = [str(a) for a in answers]
        for a in ans_ids:
            if a not in opt_ids:
                return None, f"question {i} answer {a!r} is not one of its option ids"
        if qtype in ("mcq", "truefalse") and len(ans_ids) != 1:
            return None, f"question {i} ({qtype}) needs exactly 1 answer"
        explanation = q.get("explanation", "")
        if explanation is not None and not isinstance(explanation, str):
            explanation = str(explanation)
        clean.append(
            {
                "id": str(q.get("id", f"q{i}")),
                "type": qtype,
                "stem": stem.strip(),
                "options": opt_out,
                "answers": ans_ids,
                "explanation": (explanation or "").strip(),
            }
        )
    return clean, None


class QuizGenerateTool(Tool):
    tool_id = "quiz.generate"
    name = "Quiz Generate"
    description = (
        "Validate structured quiz JSON into an interactive test. "
        "Input: questions array of {type: mcq|msq|truefalse, stem, "
        "options: [{id, text}] or [text], answers: [option_id], "
        "explanation?} (2-50 questions), optional title. Or 'data' with "
        "the extracted quiz JSON from an upstream reasoning step "
        "(pass 'data' alone, not together with questions)."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Quiz questions (2-50).",
            },
            "title": {"type": "string"},
            "data": {
                "type": "string",
                "description": (
                    "Extracted quiz JSON instead of literal questions: "
                    'a {"questions": [...]} object, typically the resolved '
                    "output of an upstream reasoning step. Pass either "
                    "'data' XOR 'questions'."
                ),
            },
        },
    }
    output_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "quiz_json": {"type": "string"},
            "question_count": {"type": "integer"},
            "title": {"type": "string"},
        },
    }
    effect_class = "sandboxed"  # type: ignore[assignment]
    cost_class = "low"

    def execute(self, request: ToolRequest) -> ToolResponse:
        title = str(request.input.get("title", "") or "").strip()[:160]
        data = request.input.get("data")
        questions_in = request.input.get("questions")
        if data is not None:
            if questions_in is not None:
                return ToolResponse(
                    tool_id=self.tool_id, ok=False, output=None,
                    error="pass 'data' alone (with title) — not together with questions",
                )
            parsed = parse_quiz_json(data)
            if parsed is None:
                return ToolResponse(
                    tool_id=self.tool_id, ok=False, output=None,
                    error="'data' must be a JSON object like {\"questions\": [...]}",
                )
            if not title and isinstance(parsed.get("title"), str):
                title = parsed["title"].strip()[:160]
            payload = parsed
        else:
            if not isinstance(questions_in, list) or not questions_in:
                return ToolResponse(
                    tool_id=self.tool_id, ok=False, output=None,
                    error="'questions' must be a non-empty array (or pass 'data' for extracted JSON)",
                )
            payload = {"questions": questions_in, "title": title}
        questions, err = validate_quiz(payload)
        if err is not None or questions is None:
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error=err or "invalid quiz",
            )
        quiz_obj: dict[str, Any] = {
            "title": title or "Quiz",
            "questions": questions,
        }
        quiz_json = json.dumps(quiz_obj, ensure_ascii=False)
        summary = f"Quiz ready: {len(questions)} questions — take the test below."
        return ToolResponse(
            tool_id=self.tool_id,
            ok=True,
            output=summary,
            data={
                "quiz_json": quiz_json,
                "question_count": len(questions),
                "title": title or "Quiz",
            },
        )
