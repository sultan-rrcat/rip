# PLAN — Layered Planning: fix mega-prompt hallucination (trace `ecd93eb4`)

> Handoff doc. Status: IMPLEMENTED 2026-09-17 (commits 558524c, 037160c,
> 50dc686, cd2cf4c, 7196983, 299aa53 + docs). L2b specialist micro-prompts
> deferred (unknown intents use L3 mega-prompt).

## 1. Problem

* Request `compare both report and rank them based on complexity` produced 3 steps:
  `1=rag.query(Fire)`, `2=rag.query(Indus)` (both `SUCCESS` with chunks),
  `3=reasoning` with message `Using retrieved chunks from step 1... and step 2...`
  — prose reference, **no `{{1}} {{2}}` placeholders**, `depends_on=[]`.
* Evidence (Langfuse trace `ecd93eb4` / run `ae7848ac-c0df-4aa4-ac9d-dd120f700aff`):
  - `step:3 start 10:37:14.011` parallel with `1/2`, not after `~10:37:23` → no dependency edge.
  - `llm.generate_stream 56438410` input = system context + bare message, zero chunks.
  - Model correctly asked user to paste reports; aggregator hid `1/2` (`chunks`)
    and showed `3` verbatim as `success` per ADR-023.
* Root causes:
  1. `backend/app/orchestration/planner.py:79-321` single ~243-line prompt + full
     manifests + Rules 1-9 + Examples A-F. No `comparison fan-in` pattern → model
     improvises. Prompt grows per fix → overload → hallucination.
  2. `validator.py:250-295 _check_reasoning_grounding` only checks
     `depends_on!=[]`; empty-deps + prose mention passes silently.
     `plan.py:260-276` auto-wires `placeholder→depends_on` one-way only; prose
     triggers nothing. `plan_graph.py:406-414` backstop has the same gap.

## 2. Agreed direction (locked with user)

* +1 cheap router LLM call OK.
* Rigidity = hybrid: deterministic builders for top intents, specialist
  micro-prompts for the rest.
* Fallback chain: `router → builder/specialist → EXISTING mega-prompt (unchanged)
  → ReAct loop (only if mega-prompt also fails)`.
* Canonical comparison shape: `2×rag.query + 1×reasoning` fan-in (keep `2+1`).

## 3. Target chain

```text
request → L0 fast-path (greeting/empty → 1×reasoning, no LLM)
 → L1 router.py {intent, slots, confidence}
 → L2a builders/ (code-built DAG) | L2b planners/ (pruned per-intent prompt)
 → validate fail → L3 planner.py mega-prompt as-is
    (+ existing RETRY FEEDBACK, max 2 attempts, engine.py:82)
 → 2nd fail (plan_error) → L4 react.py loop (max 6 iters)
 → validator → plan_graph → aggregator
```

Clarifications/cancel never enter retry/ReAct (`engine.py:344-354,372-379`).

## 4. Work items

1. `orchestration/intents.py` (new): enum `chat, qa_single, compare_multi,
   summarize, summarize_plot, plot_standalone, report, convert_one/all/ambiguous,
   quiz, code, image, vision, unknown`; threshold `0.6 → unknown`.
2. `orchestration/router.py` (new): ~30-line prompt, `generate_structured`
   schema `{intent, slots:{queries,title,format}, confidence}`, `temperature=0`;
   Langfuse `router` span via `manual_span(trace_context)` like `engine.py:177-184`.
3. `orchestration/builders/` (new): deterministic factories; `compare_multi` =
   `N×rag.query(top_k=8,chunks)` + terminal
   `reasoning(depends_on=all, message和发展 with {{1}} {{2}})`; plus `qa_single,
   summarize_plot 4-step, convert_all("*")/one(literal id), quiz 2-step`.
   Router slots fill query/title strings only — LLM never invents DAG wiring.
4. `orchestration/planners/` (split): one file per intent (relevant tools +
   1 example each); dispatcher prunes `registry.manifest()` subset.
   Keep `planner.py` untouched as L3 fallback import.
5. `engine.py` (additive only): insert `router` node, `validate→L3→react` edges;
   preserve `_validation_feedback`, `_execution_feedback`, trivial `[]→reasoning`
   repair, `plan` SSE `attempt` field.
6. `orchestration/react.py` (new, L4): thought→action→observation loop reusing
   `_make_step_node`, `_run_step_body`, `_step_timeout_ms`, cancel event,
   `parent_span_ctx` nesting, `merge_dicts` state. Max 6 iterations, per-action
   validator primitives, final `answer` or joined errors.
7. Validator (additive, still pure/no-LLM): per-shape asserts + narrow
   prose-mention heuristic gated on `rag.query` siblings
   (`retrieved chunks|step\s+\d+` without `{{}}` → reject with fix hint).
   No semantic LLM-check (deferred: violates `validator.py:1-10` + offline +
   latency; see §6).
8. Tests: router accuracy table; builder shapes (compare fan-in has
   `{{1}}{{2}}`+edges); prose-fan-in reject; L3→L4 handoff on double-reject;
   ReAct cap/cancel; replay compare trace (expect `step:3 start > 1/2 end`,
   chunks in `generate_stream` input, `shown=["3"] hidden=["1","2"]`).
   Run `pytest` (`DB_HOST=127.0.0.1`, `OLLAMA_BASE_URL=http://localhost:11434`)
   + `ruff`.
9. Docs: new `ADR-029`, `ARCHITECTURE.md §4` delta, `CAVEATS.md` planner section,
   `CHANGELOG.md` entry.

## 5. Files to touch

* New: `intents.py, router.py, builders/*.py, planners/*.py, react.py`, tests.
* Edit (additive): `engine.py` (nodes/edges only), `validator.py` (extra checks), docs.
* Do NOT touch: `plan_graph.py` execution, `aggregator.py` contract, `plan.py`
  repairs, `providers/`, DB schema.

## 6. What "semantic grounding LLM-check" means (deferred)

Current validator is syntactic: regex `\{\{\s*id\s*\}\}` + `depends_on` edge.
A semantic check would spend an extra LLM call asking
"does step 3 semantically need steps 1/2?" Deferred because it violates
validator pure/no-LLM invariant (`validator.py:1-10`, `ARCHITECTURE.md`),
doubles latency (~40-50s/plan on office model), and adds Ollama contention.
The narrow regex + deterministic builders cover the observed failure cheaper.

## 7. Risks / tradeoffs

* Router misroute → caught by validator → L3/L4 recover.
* Worst-path latency (router + 2 plans + up to 6 ReAct steps, each ≤120s agent /
  30s tool) — last-resort only; happy path is 1-2 calls.
* Single Ollama contention — L0 + L2a avoid LLM where possible.
* Prompt duplication during migration — sunset duplicates after trace-measured
  retry conversion.

## 8. Resume prompt (paste into fresh session, Build mode)

> Implement PLAN-layered-planning (this file):
> router+builders, planner split, L3-unchanged fallback, ReAct-on-double-fail.
> Respect offline Ollama-only, validator pure/no-LLM, ADR-028 2-attempt budget,
> `notebook_id` injection, `delta` live-only. Verify with pytest+ruff and
> compare-trace replay (`compare both reports...` → `router=compare_multi`,
> `step:3 start > steps 1/2 end`, chunks present in step-3 input).
