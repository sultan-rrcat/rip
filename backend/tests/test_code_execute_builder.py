"""L2 code-execute DAG (ADR-050): run/test/execute via sandbox + report.

DB-free: dispatch and plan shape are pure; the validator runs against
fake providers; the aggregator/artifacts checks use in-memory results.
"""

from __future__ import annotations

import base64
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from app.agents.base import StepStatus
from app.agents.coding import CodingAgent
from app.agents.reasoning import ReasoningAgent
from app.agents.registry import AgentRegistry
from app.artifacts import collect_artifacts, resolve_artifact
from app.core.config import settings
from app.orchestration.aggregator import Aggregator
from app.orchestration.builders import build, build_code_execute
from app.orchestration.intents import Intent
from app.orchestration.plan import PlanStep
from app.orchestration.plan_graph import _step_timeout_ms
from app.orchestration.results import ExecutionResult, StepResult
from app.orchestration.router import RouterResult
from app.orchestration.validator import PlanValidator
from app.providers.base import ModelProvider
from app.tools.registry import get_default_tool_registry

SNAPSHOT = (
    "2 file(s): app.py [ready:code] id=11111111-1111-1111-1111-111111111111; "
    "util.py [ready:code] id=22222222-2222-2222-2222-222222222222"
)


class _P(ModelProvider):
    def generate(self, *a, **k): raise AssertionError
    def generate_stream(self, *a, **k): raise AssertionError
    def generate_structured(self, *a, **k): raise AssertionError
    def embed(self, *a, **k): raise AssertionError
    def list_available_models(self): return []
    def served_model(self, m): return m


def _validator() -> PlanValidator:
    agents = AgentRegistry()
    agents.register(ReasoningAgent(_P()))
    agents.register(CodingAgent(_P()))
    return PlanValidator(agents, get_default_tool_registry())


def _code_route(file_hint: str = "") -> RouterResult:
    return RouterResult(intent=Intent.CODE, confidence=0.9,
                       routed_by="llm", file_hint=file_hint)


@pytest.mark.parametrize("ask", [
    "run the tests in app.py",
    "execute the app and report",
    "pytest app.py",
    "fix the failing test",
    "lint app.py",
])
def test_execute_verbs_build_sandbox_dag(ask) -> None:
    plan = build(ask, _code_route("app.py"), SNAPSHOT)
    assert plan is not None and len(plan.steps) == 2
    first, second = plan.steps
    assert first.tool_id == "code.sandbox"
    assert first.input["task"] == ask
    assert first.input["file_names"] == ["app.py"]
    assert first.input["timeout_ms"] == min(settings.sandbox_timeout_ms, 480000)
    assert second.agent_id == "coding"
    assert second.depends_on == ["1"]
    assert "{{1}}" in str(second.input)
    assert (second.expected_output_type or "").lower() == "answer"
    _validator().validate(plan)  # grounding + required fields hold


def test_review_verbs_stay_generate_only() -> None:
    plan = build("review the test file app.py", _code_route("app.py"), SNAPSHOT)
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].agent_id == "coding"
    assert not any(s.tool_id for s in plan.steps)
    _validator().validate(plan)


def test_generation_without_execute_verb_stays_generate_only() -> None:
    plan = build("write a quicksort in python", _code_route(), None)
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].agent_id == "coding"
    _validator().validate(plan)


def test_named_but_missing_is_clarification() -> None:
    plan = build("run the tests in nope.py", _code_route("nope.py"), SNAPSHOT)
    assert plan is not None and len(plan.steps) == 1
    assert (plan.steps[0].expected_output_type or "").lower() == "clarification"
    _validator().validate(plan)


def test_greenfield_execute_runs_task_only() -> None:
    plan = build("run pytest", _code_route(), None)
    assert plan is not None and len(plan.steps) == 2
    assert plan.steps[0].tool_id == "code.sandbox"
    assert "file_names" not in plan.steps[0].input
    _validator().validate(plan)


def test_build_code_execute_needs_no_disk() -> None:
    # Plan-time never touches the upload dir: content flows at runtime via
    # `docker cp`, so the builder only resolves names for the file scope.
    plan = build_code_execute("run tests", "app", SNAPSHOT, "nb-x")
    assert plan is not None and len(plan.steps) == 2
    assert plan.steps[0].input["file_names"] == ["app.py"]


def test_aggregator_hides_sandbox_shows_report() -> None:
    plan = build("run the tests", _code_route("app.py"), SNAPSHOT)
    assert plan is not None
    result = ExecutionResult(trace_id="t", step_results=[
        StepResult(step_id="1", agent_id="code.sandbox",
                   status=StepStatus.SUCCESS,
                   output="sandbox model=m staged=1 changed=1\nALL GREEN"),
        StepResult(step_id="2", agent_id="coding",
                   status=StepStatus.SUCCESS,
                   output="All tests pass (3 passed)."),
    ])
    agg = Aggregator().aggregate(plan, result)
    assert agg.status == "success"
    assert agg.summary == "All tests pass (3 passed)."
    assert agg.shown == ["2"]
    assert agg.hidden == ["1"]


def test_artifacts_collect_sandbox_files(tmp_path) -> None:
    payload = base64.b64encode(b"print('fixed')\n").decode("ascii")
    result = ExecutionResult(trace_id="t", step_results=[
        StepResult(step_id="1", agent_id="code.sandbox",
                   status=StepStatus.SUCCESS, output="changed=1",
                   data={"changed_files": ["app.py"],
                         "sandbox_files": [{"filename": "app.py",
                                            "b64": payload}]}),
    ])
    found = collect_artifacts(result.step_results, upload_dir=str(tmp_path),
                              notebook_id="nb", run_id="run1")
    assert len(found) == 1
    assert found[0]["kind"] == "code"
    assert found[0]["filename"].endswith("app.py")
    resolved = resolve_artifact(upload_dir=str(tmp_path), notebook_id="nb",
                                run_id="run1",
                                artifact_id=found[0]["artifact_id"])
    assert resolved is not None
    path, _, mime = resolved
    assert path.read_bytes() == b"print('fixed')\n"
    assert "python" in mime or mime == "text/plain"


def test_sandbox_step_budget_covers_tool_budget() -> None:
    sbx = PlanStep(step_id="1", tool_id="code.sandbox",
                   input={"task": "run tests"},
                   expected_output_type="text")
    assert _step_timeout_ms(sbx, 120000) >= settings.sandbox_timeout_ms
    rag = PlanStep(step_id="1", tool_id="rag.query",
                   input={"query": "q"}, expected_output_type="chunks")
    assert _step_timeout_ms(rag, 120000) == 120000
