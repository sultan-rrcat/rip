"""L2a deterministic builders — code-built DAGs, no DAG-LLM.

For intents in DETERMINISTIC_INTENTS the plan shape is fixed; only slot
values (queries, request text) vary. Wiring (depends_on + {{id}}
placeholders) is set by construction, so the ecd93eb4 failure class
(prose mention of steps without placeholders) cannot occur.
"""

from __future__ import annotations

import uuid

from app.orchestration.intents import Intent
from app.orchestration.plan import Plan, PlanStep
from app.orchestration.router import RouterResult


def build_chat(request_text: str) -> Plan:
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                agent_id="reasoning",
                input={"message": request_text},
                expected_output_type="text",
            )
        ],
    )


def build_qa_single(query: str, request_text: str) -> Plan:
    return Plan(
        plan_id=str(uuid.uuid4()),
        goal=request_text,
        steps=[
            PlanStep(
                step_id="1",
                tool_id="rag.query",
                input={"query": query or request_text, "top_k": 8},
                expected_output_type="chunks",
            ),
            PlanStep(
                step_id="2",
                agent_id="reasoning",
                input={
                    "message": (
                        f"Answer the user's request using ONLY these retrieved "
                        f"chunks {{{{1}}}}. Say 'not in the documents' when the "
                        f"chunks are empty. Request: {request_text}"
                    )
                },
                depends_on=["1"],
                expected_output_type="answer",
            ),
        ],
    )


def build_compare_multi(queries: list[str], request_text: str) -> Plan:
    """2+ parallel rag.query steps fanning into one reasoning step.

    Grounding is structural: every rag step id appears in both depends_on
    and as a {{id}} placeholder in the reasoning message.
    """
    sources = [q for q in (queries or []) if q.strip()]
    while len(sources) < 2:
        sources.append(request_text)
    sources = sources[:5]  # parallelism budget: ≤5 siblings
    steps: list[PlanStep] = []
    for i, q in enumerate(sources, start=1):
        steps.append(
            PlanStep(
                step_id=str(i),
                tool_id="rag.query",
                input={"query": q, "top_k": 8},
                expected_output_type="chunks",
            )
        )
    dep_ids = [s.step_id for s in steps]
    refs = " ".join(f"{{{{{sid}}}}}" for sid in dep_ids)
    steps.append(
        PlanStep(
            step_id=str(len(steps) + 1),
            agent_id="reasoning",
            input={
                "message": (
                    f"Using ONLY these retrieved chunks ({refs}), address the "
                    f"request. Say 'not in the documents' for anything the "
                    f"chunks do not cover. Request: {request_text}"
                )
            },
            depends_on=dep_ids,
            expected_output_type="answer",
        )
    )
    return Plan(plan_id=str(uuid.uuid4()), goal=request_text, steps=steps)


def build(request_text: str, route: RouterResult) -> Plan | None:
    """Dispatch router result to a deterministic builder.

    Returns None for intents without a fixed shape — caller falls through
    to L2b specialist / L3 mega-prompt.
    """
    if route.intent is Intent.CHAT:
        return build_chat(request_text)
    if route.intent is Intent.QA_SINGLE:
        query = route.queries[0] if route.queries else request_text
        return build_qa_single(query, request_text)
    if route.intent is Intent.COMPARE_MULTI:
        return build_compare_multi(route.queries, request_text)
    return None
