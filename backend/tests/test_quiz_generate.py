"""Quiz tool + builder tests (no Ollama, no DB, no langfuse)."""

from __future__ import annotations

import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.tools.base import ToolRequest, ToolResponse
from app.tools.quiz_generate import QuizGenerateTool, parse_quiz_json


def _qs(n=3, qtype="mcq"):
    return [
        {
            "type": qtype,
            "stem": f"Q{i}?",
            "options": ["A", "B", "C", "D"],
            "answers": ["o1"],
            "explanation": "E",
        }
        for i in range(n)
    ]


def _quiz_validator():
    """PlanValidator with stub registries (avoids rag_query → langfuse chain)."""
    from app.agents.reasoning import ReasoningAgent
    from app.agents.registry import AgentRegistry
    from app.orchestration.validator import PlanValidator
    from app.providers.base import ModelProvider
    from app.tools.quiz_generate import QuizGenerateTool
    from app.tools.registry import ToolRegistry

    class _FakeProvider(ModelProvider):
        def generate(self, model, messages, **kw):
            return "ok"

        def generate_structured(self, model, messages, schema, **kw):
            return {"intent": "quiz"}

        def embed(self, model, text):
            raise NotImplementedError

        def list_available_models(self):
            return [{"id": "fake"}]

    provider = _FakeProvider()
    agents = AgentRegistry()
    agents.register(ReasoningAgent(provider))
    tools = ToolRegistry()

    class _FakeRagQuery(QuizGenerateTool):
        tool_id = "rag.query"
        name = "Rag Query"
        description = "fake rag.query for validator tests"

        def execute(self, request):  # type: ignore[override]
            return ToolResponse(tool_id="rag.query", ok=True, output="(no chunks retrieved)")

    tools.register(_FakeRagQuery())
    tools.register(QuizGenerateTool())
    return PlanValidator(agents, tools)


def test_quiz_tool_accepts_literal_questions() -> None:
    t = QuizGenerateTool()
    r = t.execute(ToolRequest(tool_id="quiz.generate", input={"questions": _qs(3), "title": "T"}))
    assert r.ok
    assert r.data["question_count"] == 3
    payload = json.loads(r.data["quiz_json"])
    assert payload["title"] == "T"
    assert len(payload["questions"]) == 3
    assert r.output and "3 questions" in r.output


def test_quiz_tool_rejects_too_few() -> None:
    t = QuizGenerateTool()
    r = t.execute(ToolRequest(tool_id="quiz.generate", input={"questions": _qs(1)}))
    assert not r.ok and "at least 2" in (r.error or "")


def test_quiz_tool_rejects_bad_answer_id() -> None:
    t = QuizGenerateTool()
    bad = _qs(2)
    bad[0]["answers"] = ["o9"]
    r = t.execute(ToolRequest(tool_id="quiz.generate", input={"questions": bad}))
    assert not r.ok and "o9" in (r.error or "")


def test_quiz_tool_rejects_mcq_multi_answer() -> None:
    t = QuizGenerateTool()
    bad = _qs(2)
    bad[0]["answers"] = ["o1", "o2"]
    r = t.execute(ToolRequest(tool_id="quiz.generate", input={"questions": bad}))
    assert not r.ok and "exactly 1" in (r.error or "")


def test_quiz_tool_accepts_msq_multi_answer() -> None:
    t = QuizGenerateTool()
    qs = _qs(2, "msq")
    for q in qs:
        q["answers"] = ["o1", "o2"]
    r = t.execute(ToolRequest(tool_id="quiz.generate", input={"questions": qs}))
    assert r.ok


def test_quiz_tool_data_path_rejects_mixed_input() -> None:
    t = QuizGenerateTool()
    r = t.execute(
        ToolRequest(
            tool_id="quiz.generate",
            input={"questions": _qs(2), "data": '{"questions": []}'},
        )
    )
    assert not r.ok and "alone" in (r.error or "")


def test_quiz_tool_data_path_parses_fenced_json() -> None:
    t = QuizGenerateTool()
    inner = json.dumps({"questions": _qs(2)})
    r = t.execute(ToolRequest(tool_id="quiz.generate", input={"data": f"```json\n{inner}\n```"}))
    assert r.ok
    assert r.data["question_count"] == 2


def test_parse_quiz_json_envelope() -> None:
    parsed = parse_quiz_json(json.dumps({"questions": _qs(2)}))
    assert parsed is not None and len(parsed["questions"]) == 2


def test_parse_quiz_params() -> None:
    from app.orchestration.builders import parse_quiz_params

    assert parse_quiz_params("generate 5 MSQs on physics") == (5, "msq")
    assert parse_quiz_params("make 20 MCQs") == (20, "mcq")
    assert parse_quiz_params("quiz me") == (10, "mcq")
    assert parse_quiz_params("true false test please") == (10, "truefalse")


def test_quiz_builder_grounded_shape() -> None:
    from app.orchestration.builders import build
    from app.orchestration.intents import Intent
    from app.orchestration.router import RouterResult

    validator = _quiz_validator()
    route = RouterResult(intent=Intent.QUIZ, confidence=1.0, routed_by="rule")
    plan = build("generate 5 MCQs on physics", route, "1 file(s): a.pdf [ready] id=aaa")
    assert plan is not None
    kinds = [(s.tool_id or s.agent_id, s.expected_output_type) for s in plan.steps]
    assert kinds[0] == ("rag.query", "chunks")
    assert kinds[1] == ("reasoning", "quiz_json")
    assert kinds[2] == ("quiz.generate", "quiz")
    assert plan.steps[1].input["message"].count("exactly 5") >= 1
    assert plan.steps[2].input["data"] == "{{2}}"
    assert plan.steps[2].depends_on == ["2"]
    validator.validate(plan)
    rag = plan.steps[0]
    assert rag.input.get("verbatim") is True
    assert rag.input.get("mode") == "overview"


def test_quiz_builder_general_knowledge_shape() -> None:
    from app.orchestration.builders import build
    from app.orchestration.intents import Intent
    from app.orchestration.router import RouterResult

    validator = _quiz_validator()
    route = RouterResult(intent=Intent.QUIZ, confidence=1.0, routed_by="rule")
    plan = build("quiz me on python", route, "(no documents)")
    assert plan is not None and len(plan.steps) == 2
    assert plan.steps[0].agent_id == "reasoning"
    assert plan.steps[0].expected_output_type == "quiz_json"
    assert plan.steps[1].tool_id == "quiz.generate"
    assert all(s.tool_id != "rag.query" for s in plan.steps)
    validator.validate(plan)


def test_quiz_validator_rejects_chunks_grounding() -> None:
    from app.orchestration.plan import Plan, PlanStep
    from app.orchestration.validator import PlanValidationError

    validator = _quiz_validator()
    bad = Plan(
        plan_id="p",
        goal="g",
        steps=[
            PlanStep(step_id="1", tool_id="rag.query", input={"query": "x"}, expected_output_type="chunks"),
            PlanStep(
                step_id="2",
                tool_id="quiz.generate",
                input={"data": "{{1}}"},
                depends_on=["1"],
                expected_output_type="quiz",
            ),
        ],
    )
    try:
        validator.validate(bad)
    except PlanValidationError as e:
        assert "quiz_json" in str(e)
    else:
        raise AssertionError("expected PlanValidationError")


def test_quiz_aggregator_hides_quiz_json() -> None:
    from app.agents.base import StepStatus
    from app.orchestration.aggregator import Aggregator
    from app.orchestration.plan import Plan, PlanStep
    from app.orchestration.results import ExecutionResult, StepResult

    plan = Plan(
        plan_id="p",
        goal="g",
        steps=[
            PlanStep(step_id="1", agent_id="reasoning", input={}, expected_output_type="quiz_json"),
            PlanStep(step_id="2", tool_id="quiz.generate", input={}, expected_output_type="quiz"),
        ],
    )
    result = ExecutionResult(
        trace_id="t",
        step_results=[
            StepResult(step_id="1", agent_id="reasoning", status=StepStatus.SUCCESS, output='{"questions": []}'),
            StepResult(step_id="2", agent_id="quiz.generate", status=StepStatus.SUCCESS, output="Quiz ready: 10 questions"),
        ],
    )
    agg = Aggregator().aggregate(plan, result)
    assert agg.shown == ["2"]
    assert agg.hidden == ["1"]
    assert "Quiz ready" in agg.summary


def test_quiz_artifacts_collect() -> None:
    import json as _json

    from app.agents.base import StepStatus
    from app.artifacts import collect_artifacts
    from app.orchestration.results import StepResult

    quiz_json = _json.dumps(
        {
            "title": "T",
            "questions": [
                {
                    "id": "q1",
                    "type": "mcq",
                    "stem": "S?",
                    "options": [{"id": "o1", "text": "A"}, {"id": "o2", "text": "B"}],
                    "answers": ["o1"],
                    "explanation": "",
                },
                {
                    "id": "q2",
                    "type": "mcq",
                    "stem": "S2?",
                    "options": [{"id": "o1", "text": "A"}, {"id": "o2", "text": "B"}],
                    "answers": ["o2"],
                    "explanation": "",
                },
            ],
        }
    )
    res = StepResult(
        step_id="3",
        agent_id="quiz.generate",
        status=StepStatus.SUCCESS,
        output="Quiz ready",
        data={"quiz_json": quiz_json, "question_count": 2, "title": "T"},
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        found = collect_artifacts([res], upload_dir=tmp, notebook_id="nb", run_id="run1")
        assert len(found) == 1
        assert found[0]["kind"] == "quiz"
        assert found[0]["filename"].endswith(".quiz.json")
