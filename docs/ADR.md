# Architecture Decision Records (ADR)

Active decisions first; superseded merge-era history is collapsed at the bottom.

---

## ADR-001: FastAPI + Python backend

- **Status:** Accepted
- **Context:** The backend must integrate heavy ML libraries (Docling, sentence-transformers/BGE) and serve long-running streaming responses.
- **Decision:** FastAPI on Python 3.11+, with an async lifespan that loads ML models once at startup.
- **Consequences:** SSE streaming, Python ML ecosystem access, auto-generated OpenAPI docs. Startup fails fast if local weights are unavailable.

## ADR-004: Docling → Markdown, header-based chunking

- **Status:** Accepted
- **Context:** Sectioned documents (papers, reports) need parsing that preserves structure and meaningful boundaries.
- **Decision:** Parse with Docling to Markdown, then chunk on Markdown headers (`#/##/###` → H1/H2/H3) via `MarkdownHeaderTextSplitter`.
- **Consequences:** Section-aware chunk metadata (`source`, H1/H2/H3) feeds retrieval and source display. Output quality bounds to Docling's parse.

## ADR-005: Background-task ingestion pipeline

- **Status:** Accepted
- **Context:** Ingestion (parse, embed) is slow and must not block HTTP.
- **Decision:** `POST /api/files/{file_id}/process` enqueues `run_rag_pipeline` as a FastAPI `BackgroundTasks` job; the frontend polls file status until `ready`/`error`.
- **Consequences:** Responsive UX, but no progress reporting and no durable queue — a restart mid-ingestion can strand a file in `processing`.

## ADR-007: Single vector + full-text retrieval path (no knowledge graph)

- **Status:** Accepted
- **Context:** A Neo4j knowledge graph required a second datastore, per-chunk LLM extraction, and a slow LLM-in-the-loop retrieval path.
- **Decision:** `VectorRAG` is the single retrieval path: pgvector cosine + full-text rank fusion (RRF) + BGE rerank. No graph store, no `NEO4J_*` config.
- **Consequences:** One datastore, faster ingestion, simpler deployment. Revisit via new ADR if cross-document traversal becomes a requirement.

## ADR-014: Postgres-backed runs with SSE replay

- **Status:** Accepted
- **Context:** Runs must survive page refresh and notebook switches.
- **Decision:** Persist run state + structural SSE events (`runs` + `run_events`). `delta` frames are live-only (see ADR-020). The SSE endpoint replays persisted events on reconnect; the frontend dedupes by `seq`. Cancel via `POST /v1/runs/{id}/cancel`.
- **Consequences:** Refresh-safe runs. Only the stop button terminates a run.

## ADR-015: One notebook = one conversation (no conversations table)

- **Status:** Accepted
- **Context:** A separate `conversations` table duplicated the notebook with a redundant 1:1 join and dual ownership.
- **Decision:** Messages link by `notebook_id` only. Internal memory fields (`conversation_summary`, `summary_message_count`) live on `notebooks`.
- **Consequences:** Simpler model; conversation state reads straight off the notebook row.

## ADR-016: Context-window-based summary

- **Status:** Accepted
- **Context:** Turn-count heuristics ignore message length and model context size.
- **Decision:** Fold history into `notebooks.conversation_summary` (Ollama-generated, internal, not user-visible) when tokens reach ~70% of the model context window (`summary_threshold_pct`, default 0.7). Distinct from the SSE `summary` event (the final answer).
- **Consequences:** Model-aware memory. `memory_window_size` remains an advisory cap; the token budget governs.

## ADR-017: Direct capability inheritance + VectorRAG singleton

- **Status:** Accepted
- **Context:** Dynamic plugin wrappers add indirection, and re-instantiating `VectorRAG` per query reloads heavy PyTorch weights.
- **Decision:** Agents/tools/providers inherit directly from base classes with explicit registry factories. `rag.query` reuses the lifespan `VectorRAG` singleton; the orchestrator injects `notebook_id` from the run (never LLM-generated).
- **Consequences:** No plugin scaffolding, no per-query model reload spikes.

## ADR-018: No query rewrite

- **Status:** Accepted
- **Context:** A pre-retrieval rewrite step cost an extra LLM call per turn.
- **Decision:** No rewrite layer. The Planner produces search intent inside plan steps.
- **Consequences:** One fewer LLM call per turn; planner prompt quality carries retrieval intent.

## ADR-019: File-based artifacts

- **Status:** Accepted (path + rendering amended — chart SVG fix)
- **Context:** Tool outputs must survive refresh as downloadable files scoped to notebooks.
- **Decision:** Write to `{upload_dir}/{notebook_id}/artifacts/{run_id}/{step_id}/{filename}` plus per-run `index.json`; SSE `artifacts` carries download URLs, never inline base64. Served by `GET /v1/runs/{id}/artifacts/{artifact_id}`.
- **Consequences:** Durable downloads. Charts (`kind: "chart"`, `image/svg+xml`) render inline in chat as `<img>` over the same URL — raw SVG markup is never injected into the DOM. Chart refs are persisted on the assistant `messages.artifacts` (frontend-owned) so previews survive reload.

## ADR-020: Structural SSE persistence (no delta replay)

- **Status:** Accepted (amended — streaming + context tiers, run `4efaec2b`)
- **Context:** Persisting per-token `delta` frames would bloat `run_events` and flood reconnects. Final-step-only delta routing additionally stalled the main bubble on multi-step runs (early-step tokens hidden in a side panel, then a summary jump).
- **Decision:** Persist structural events + final text only. Reconnect rebuilds text from `step_completed`/`summary`. Live, every `delta` streams into the main bubble (which matches the concatenated summary); the Steps collapsible is a mirror-only, ephemeral view cleared on `run_completed` — never a routing target deltas can be dropped into. Memory `context` is scoped per step tier: planner + terminal prose agent steps get full context; intermediate agent steps get task + resolved upstream placeholders only (the planner threads follow-up references into subtask messages); tool steps get none (`notebook_id` only); `fallback_message` injects into agent steps lacking `message`, never tools.
- **Consequences:** Small DB footprint; frontend must never expect delta replay. Main-bubble text always converges to the persisted summary; per-step detail is live-only.

## ADR-021: Run worker contract

- **Status:** Accepted
- **Context:** Message ownership and memory boundaries need one enforceable contract.
- **Decision:** The worker always orchestrates; loads memory → passes `context=`; persists updated summary; emits `sources` on `rag.query`; **never writes `messages`** (frontend-owned).
- **Consequences:** Clear boundary: frontend = chat rows, backend = runs + internal memory.

## ADR-022: Sources SSE event

- **Status:** Accepted
- **Context:** Citations need a first-class path in the runs protocol.
- **Decision:** Emit `{type:"sources", sources:[{source, section}]}` on `rag.query` completion; persist; frontend stores them on the assistant message.
- **Consequences:** Grounded citations without a separate pre-call.

## ADR-023: Deterministic aggregator (no LLM synthesis)

- **Status:** Accepted (amended — type-aware concat, run `4efaec2b`)
- **Context:** LLM synthesis per run costs latency/money and is hard to test. Final-step-only summaries additionally drop terminal text (a `rag → reasoning → plot` run persisted only the chart placeholder, losing a correct summary), while blind concatenation leaks intermediate machine outputs (chunk dumps, `numbers` feeds) into chat.
- **Decision:** Type-aware assembly over `expected_output_type`: HIDE intermediates (`chunks`/`numbers`, `notebook.inspect` probe) unless the sole output (anti-blank fallback to last success); SHOW terminals (`answer`/`summary`/`text`/`document`/`chart`/`clarification`, plus unknown types — fail-visible, never fail-blank; `summary` normalizes to answer). Single SHOW → verbatim (no `Step N` prefix); multi-SHOW → labeled concatenation (`Step <id> (<executor>): ...`); clarification → verbatim; all-failed/empty → joined errors; partial runs append every failure line. Exception: step outputs carrying chart SVG (`<svg`) aggregate to the placeholder `Chart generated — see Artifacts below.` — the SVG bytes stay on `StepResult.output` and travel via the SSE `artifacts` event.
- **Consequences:** Predictable and testable; less polish on multi-step synthesis. Chat and persisted messages never store multi-KB raw SVG. A future LLM Synthesizer stays **deferred opt-in** behind a flag (deterministic remains default); revisit only if polish proves insufficient after v2.

## ADR-024: Admin health stub

- **Status:** Accepted
- **Context:** No admin console exists; plugin reload endpoints have no backend.
- **Decision:** `GET /v1/admin/health` only. No `/plugins`, no `/reload`.
- **Consequences:** Minimal admin surface.

## ADR-025: Opt-in Langfuse tracing

- **Status:** Accepted
- **Context:** Operators need per-run visibility (planner output, step I/O, generations, token usage) without changing run behavior.
- **Decision:** Ported observability layer (`observability/langfuse.py`, `providers/tracing.py` via `wrap_provider()` at composition time, never on test doubles). One trace per run worker (`session_id = notebook_id`); spans `run/plan/step:{id}/aggregate`. Strictly opt-in (`LANGFUSE_ENABLED=true` + keys + restart); disabled path is behavior-identical.
- **Consequences:** Tracing data leaves the box only when the operator enables it; offline-first default preserved.

## ADR-026: Trivial-plan fallback (no empty plans)

- **Status:** Accepted (2026-09-13, commit `e58e18b`)
- **Context:** The planner prompt allowed `steps: []` for trivial/conversational requests. Empty DAGs execute zero steps, and the deterministic aggregator reports `failed: No steps were executed` — so greetings like "Hii there" failed (Langfuse-verified).
- **Decision:** (1) Planner rule 6 now requires exactly one `agent_id="reasoning"` step carrying the user request verbatim — never an empty array. (2) Defense in depth: the engine repairs any still-trivial plan to a single `reasoning` step (first registered agent if `reasoning` is absent) before execution.
- **Consequences:** Conversational turns succeed via one reasoning call. The aggregator's empty-is-failed rule is unchanged (still correct for genuinely unexecutable plans).

## ADR-027: Dynamic document awareness (notebook.inspect + doc.convert)

- **Status:** Accepted
- **Context:** The planner was blind to uploads: `Planner.plan()` received only the user text + memory, so factual questions over uploaded docs were answered from parametric knowledge (single `reasoning` step) and "convert to docx" had no discovery path — `doc.generate` synthesizes reports from answer text and cannot read files.
- **Decision:** (1) New read-only `notebook.inspect` tool listing `{file_id, file_name, file_size, file_status}` for the run's notebook (`notebook_id` injected, never LLM-generated). (2) New sandboxed `doc.convert(file_id, target_format=md|docx|pdf)` tool for exact file conversion via the ingest loaders (Docling Markdown export for PDF/DOCX → markdown, then lossless render to md/docx/pdf). `file_id: "*"` converts every ready file in one step; `file_name` alias supported. (3) Thread a static file snapshot (`manager._load_memory` → `orchestrator.run(notebook_context=)` → planner prompt `Notebook documents:` section) plus routing rules: factual/QA with ready docs → `rag.query` first; plural convert-all or single named convert → single `doc.convert` with literal `file_id` from snapshot (or `"*"`) — never placeholders, never `notebook.inspect` chain; `notebook.inspect` is a freshness probe only (single step, output not chained); report-format → `doc.generate`; ambiguous convert or missing format → single `reasoning` counter-question (`needs_clarification`). Placeholder rule hardened: placeholders only carry whole-text step output; dotted access like `{{1.files[0].id}}` is forbidden. `doc.generate` vs `doc.convert` stay separate tools by intent.
- **Consequences:** Tool set grows 5→7. `doc.convert` v2 supports DOCX→markdown and convert-all via `"*"`, returns per-file `conversions` list; artifacts writer emits stem-named files (`{stepid}_{stem}.md`) and iterates conversions. Aggregator surfaces failures on partial runs. No schema change; File→Artifact only (never mutates `files`).
- **Amended (plot/report grounding, run `4efaec2b`):** a planner once emitted `plot.chart` with `depends_on` but hardcoded `values: [50, 50]` (no `{{id}}` placeholder), rendering a hallucinated 50/50 chart over real 62/38 data. Contract now: dependent plots MUST reference upstream numbers via `{{id}}` in `values` (literals + deps = rejected, fail-honest `plan_error`, never a fake chart); plot `values` refs must target `numbers`-type steps (prose/chunks cannot parse as floats); direct `rag.query(chunks) → plot.chart` edges must route through a numbers-producing reasoning step; `doc.generate` with dependencies needs an upstream `answer`/`summary`/`text` step. Standalone plots with user-given literals and standalone reports with full sections stay legal. Summarize+plot uses split branches: `rag → numbers + answer` in parallel, `plot` depending on numbers only (one reasoning step can never feed both prose and plot values). Comparison plots: one number per label in label order; multiple sources fan into ONE merging numbers step the plot solely depends on — each `values` element is a number or a lone `{{id}}` (garbled/multi-ref values rejected, run `66fd4ec3`). `plot.chart` splits placeholder-resolved comma-separated strings back into points (length checked post-split). All-hidden successes plus failures aggregate to the failure lines only. `expected_output_type` vocabulary: `chunks|answer|numbers|chart|document|text|clarification|summary` (`summary` ≡ answer; unknown types allowed-but-logged, aggregator treats them as SHOW).

## ADR-028: Bounded planner recall (one retry)

- **Status:** Superseded by ADR-032 (recall loop removed with the mega-prompt)
- **Context:** The deterministic gates (ADR-023/027) turn planner mistakes into honest failures, but the planner (`qwen2.5:14b`) repeats the same malformation across runs (garbled `",{{2}}"` plot values 3/3 despite explicit prompt rules) — each failure previously cost a full run with nothing learned. The merge-era "no replanning" rule explicitly no longer holds.
- **Decision:** One recall = two planner outputs max per run. A rejected plan replans with short validation feedback (rejected plan excerpt + exact validator message + one-line fix); a partial/failed aggregation replans with execution feedback (failed step ids + errors + prior plan, failed outputs only). Guards: clarifications never replan; cancellations suppress recall; second failure surfaces honestly; ADR-026 trivial repair sits outside the attempt budget; no resumption — retries re-execute fully. Feedback rides a `RETRY FEEDBACK` block appended after the planner examples plus an additive `attempt` field on the SSE `plan` event (second `plan` resets the frontend accumulators; attempt-1 artifact files orphan on disk).
- **Consequences:** Failure-path planning latency roughly doubles (~40–50s per plan call on the office model); execution retries re-spend RAG + step LLMs; success path unchanged. If trace-measured retry conversion stays low, stop tuning text and change the model or decoding instead.

## ADR-029: Layered planning (router + deterministic builders + ReAct fallback)

- **Status:** Amended by ADR-032 (2026-09-18): L0 fast-path deleted, L3 mega-prompt deleted (`planner.py` is a thin holder), ReAct promoted L4→L3, no recall. L0/L3-mega/L4 numbering + ADR-028 recall budget below are historical; current path is L1 Router → L2 Builders → L3 ReAct (see ADR-032).
- **Original status:** Accepted (2026-09-17, trace `ecd93eb4`)
- **Context:** The planner prompt (`planner.py`, ~243 lines + full manifests + Rules 1-9 + Examples A-F) grows with every hardening rule, and the single-shot DAG format forces the model to solve intent classification + DAG shape + placeholder wiring in one call. Observed live: a compare-two-reports request produced two good `rag.query` steps plus a reasoning step that named "step 1/2" in prose with no `{{1}} {{2}}`, ran in parallel with retrieval, and asked the user to re-upload. Appending more prompt rules does not scale.
- **Decision:** Layer the planning path, strictly additive; the existing mega-prompt is untouched as L3 fallback:
  1. L0 deterministic fast-path (`intents.classify_fast_path`): greetings/empty → single `reasoning` step, no LLM.
  2. L1 intent router (`router.py`): one tiny `generate_structured` call (`{intent, confidence}`); `<0.6` → `unknown`.
  3. L2a deterministic builders (`builders.py`): fixed DAGs for `chat/qa_single/compare_multi` — wiring set by construction. L2b specialist micro-prompts stay future work; unknown intents fall to L3.
  4. L3 mega-prompt unchanged, incl. ADR-028 one-recall budget; layered routing runs on the first attempt only so retries stay pure mega-prompt.
  5. Validator gains `_check_prose_grounding`: prose step-references without `{{id}}` are rejected, but only when `rag.query` chunks siblings exist (no false positives on benign prose).
  6. L4 ReAct fallback (`react.py`): when plan recall is exhausted, a thought → action → observation loop (max 6 iterations, idle cap 2, cooperative cancel) answers one step at a time with observations inlined — no placeholders ever. Aggregates through the standard deterministic Aggregator; failure stays honest (`OrchestrationError` with the original plan error).
- **Consequences:** Happy path costs one extra cheap call (router) or zero (fast-path/deterministic hit); worst path is bounded (router + 2 plans + ≤6 single steps). New capabilities should land as intents + builders, not prompt appendices. `test_layered.py` pins the chain; recall-test queues carry a router-miss head.
- **Observability amendment (Option A, trace-only):** the router emits a `router` span as a sibling of `plan` under `run` (explicit `run_ctx` parenting; no graph/state change), `plan` output carries `layer/intent/routed_by/confidence`, and the ReAct fallback traces as `react → react:iter-N → step:rN` with fixed `parent_span_ctx` threading. The SSE `plan` event carries additive `route: {intent, routed_by, confidence}`.

## ADR-030: File-scoped intelligent retrieval (per-file shards + overview mode)

- **Status:** Accepted (2026-09-18, trace `795abaf2` — both compare queries returned one document). Note (ADR-032): "L3 prompt" refs below are historical — the mega-prompt was deleted; `summarize_plot`/ungoverned intents are now served by L3 ReAct.
- **Context:** `rag.query` was notebook-global (SQL filtered only `notebook_id`) and `build_compare_multi` fanned out by LLM query-angle, not by file. Two generic topical queries embed similarly and both retrieve the dominant document; the post-hoc `_interleave_by_source` cannot recover chunks that never passed the rerank threshold.
- **Decision:** (1) Extend `rag.query`/`VectorRAG.retrieve_context` with optional `file_id`/`file_name` (SQL `AND file_id=...`, literals from snapshot only, never invented/placeholders) and `mode=specific|overview` (default `specific`). Overview fetches `top_k*3` candidates, applies a metadata section-keyword boost (introduction/summary/abstract/..., never slash-joined into the embedding query), then stratifies one chunk per H1 in `chunk_index` order. (2) Builders fan out per ready snapshot file (`top_k=4` each, ≤5 shards): compare → `specific` with request text; summarize/quiz → `overview` into one reduce/writer. `summarize` joins `DETERMINISTIC_INTENTS`; `>5` files fall through to L3. (3) Validator rejects empty/placeholder `file_id` and unknown `mode`. No new tool (tools stay LLM-free; reasoning agent still reduces), no schema change (`embeddings.file_id/chunk_index` already stored).
- **Consequences:** Compare/summarize/quiz scale with file count (4 chunks/file); global `qa_single` unchanged. Separate `rag.summary` tool rejected (duplicate retrieval path, larger planner menu against ADR-029).
- **Amended (empty-corpus short-circuit, trace `ea48cb30`):** deterministic builders unconditionally emitted `rag.query` even when the snapshot proved zero ready files — a factual question on an empty notebook burned retrieval for a guaranteed `(no chunks retrieved)` and forced the grounded prompt to answer "not in the documents". Builders now tri-state the snapshot (`unknown` on `None` → still retrieve; `ready` → grounded path; `empty`/`processing` → no `rag.query`): empty `qa_single` answers generally from general knowledge (verbatim request), empty summarize/compare/quiz and all processing states yield a single upload/wait clarification. Query generation moved into `rag.query` tool (router emits intent only; retrieval queries are decomposed internally via LLM), and the L3 prompt carries an explicit zero-file rule. `qa_single` uses the unified `top_k: 4`.
- **Amended (no-docs parametric plots + multi-series, trace `27dcf635`):** a plot request on an empty notebook had no legal plan — the zero-file rule covered QA/summarize/compare/quiz/convert but not plots, so L3 emitted an unguarded `plot.chart` (no `values`, rejected), then an executor-less step (recall burned), and the ReAct fallback spent 4 of 6 iterations repeating `rag.query` (empty corpus) and `code.sandbox` (`docker CLI not found`). Fixes: (1) `plot.chart` takes multi-series comparisons (`series: [{label, values}]`, ≤5 series, shared `labels`, legend; single `labels`+`values` unchanged) with per-series CSV-split and exactly-one-of validation; validator allows one placeholder per series, each to a numbers step. (2) L3 zero-file plot rule: one numbers-only reasoning step per series recalling approximate figures, then one `plot.chart` with literal labels + `series` placeholders (single-series: `values: [{{1}}]`). Plot labels/series stay L3-only — no deterministic builder can wire model-derived names by construction (placeholders carry whole text). (3) ReAct refuses `rag.query` on an empty corpus, turns repeats of a failed executor into idle turns, recalls approximate numbers via reasoning when no observation holds them, and advertises `code.sandbox` as unavailable when docker is missing; retry feedback names a missing executor explicitly.

## ADR-031: ReAct input contract + plot preference (trace `c9e59039`)

- **Status:** Accepted (2026-09-18). Note (ADR-032): "L3 prompt" refs below are historical — the mega-prompt was deleted; ReAct is now L3 (not L4 fallback).
- **Context:** `summarize_plot` stays on L3 (labels content-derived) and failed twice (nested-step + missing-placeholder plans); the ReAct fallback then burned all 6 iterations on input-shape errors — `{"agent": {"message": ...}}` for `rag.query`/`code.sandbox`, missing `target_format` for `doc.convert` — and picked `code.sandbox` over `plot.chart` for a bar-chart ask. The one successful `rag.query` (correct class table, file-scoped) was buried under joined failure lines.
- **Decision:** (1) ReAct prompt states FLAT input shapes with per-tool one-liners + WRONG/RIGHT example and hard plot rule (bar/line → `plot.chart` with literals, never `code.sandbox`). (2) `react.py` normalizes (`agent.message` unwrap, stray `tool_id` drop, `message→query`/`message→code` aliases mirroring `RagQueryTool`) and pre-flight validates required fields before execution — malformed turns get a correct-shape scratchpad hint as an idle turn without consuming a step. (3) L3 prompt unified to `top_k: 4` + explicit "BOTH numbers and answer steps MUST contain `{{1}}`" line.
- **Consequences:** Malformed-model turns no longer burn the 6-iteration budget; chart asks route to the deterministic SVG path. `summarize_plot` remains L3-only (no deterministic builder — label hallucination risk per ADR-027 stands).

## ADR-032: Remove L0 fast-path and L3 mega-prompt; L1 dispatches to L2 builders or L3 ReAct

- **Status:** Accepted
- **Context:** The mega-prompt grew with every hardening rule and forced intent classification + DAG shape + placeholder wiring into one call; the L0 fast-path saved one cheap router call on greetings at the cost of a named layer and a parallel routing path. Operator decision: accept up to 6 ReAct iterations for non-deterministic requests; route low-confidence/`unknown` directly to ReAct instead of failing honestly.
- **Decision:** (1) Delete L0 (`intents.classify_fast_path`): every request — including greetings — goes through the L1 router LLM. (2) Delete the L3 mega-prompt (`planner.py` becomes a thin provider holder; `PLAN_SCHEMA`, retry-feedback builders, and the `attempt`/`planner_feedback` recall loop are removed). (3) L1 is the sole dispatcher: `{intent, queries, confidence, file_hint, target_format}` → L2 deterministic builder on hit, else `plan_error → END` and the orchestrator runs L3 ReAct (promoted from L4) before failing honestly. Router failures fail open to ReAct. (4) No recall: the aggregate node never replans; partial/failed runs surface honestly. The SSE `plan` event keeps a constant `attempt: 1` for old clients. `summarize_plot` stays builder-less (ADR-027 label risk stands) and is served by ReAct.
- **Consequences:** Happy path costs exactly one router call + builder execution; worst path is bounded (router + ≤6 ReAct steps). Greetings now spend one router call. New capabilities land as intents + builders, never prompt appendices. `test_layered.py` pins router→builder and router→ReAct; recall tests were replaced with ReAct-fallback tests.

## ADR-033: Whole-file early return in `rag.query` (skip planner + retrieval when the scope fits)

- **Status:** Accepted
- **Context:** Every `rag.query` paid two costs before returning anything useful: one LLM call to generate 1-3 sub-queries, then the full embed → pgvector → full-text → RRF → CrossEncoder rerank pipeline. Both are wasted whenever the answer is "here is the whole document": if every chunk in scope already fits the context window, ranking cannot improve the answer, it can only drop relevant chunks (`top_k=4` of a 30-chunk file discards ~87% of the evidence). The size test is one indexed aggregate (`SUM(char_length(chunk_text))` over `idx_embeddings_file`) that reads no vectors.
- **Decision:** (1) `VectorRAG.retrieve_whole_file(notebook_id, file_id=, file_name=)` runs the SUM/COUNT probe and returns every chunk in `chunk_index` order when the scope fits, or `None` so the ranked path runs untouched. Gates: `SUM(char_length) <= int(ollama_context_window * rag_whole_file_pct) * 4` (the `len//4` estimator from `memory.py`, so retrieval and conversational memory agree on token size) and `COUNT(*) <= _WHOLE_FILE_MAX_CHUNKS` (400, a code invariant guarding what the estimator under-measures). Both file-scoped and notebook-global scopes short-circuit, in both `specific` and `overview` modes. (2) `RagQueryTool.execute` calls it before the sub-query planner, so a hit skips the LLM call and the whole retrieval pipeline; `data["whole_file"]` records which path ran. Access is `getattr`-guarded so legacy doubles fall through, and any DB error falls back rather than failing retrieval. (3) No schema change, no new tool, no new dependency.
- **Rationale for 15% (`rag_whole_file_pct`):** the budget is per-SHARD, not per-run. `build_compare_multi`/`build_summarize`/`build_quiz` fan out up to 5 file-scoped shards and `plan_graph` concatenates every shard into ONE reduce prompt, so a 40% share would let 5 shards reach ~65k tokens against a 32768 window. At 0.15 the worst case (~24.5k) still leaves room for the memory context, the system prompt and the answer. Read live from settings so tests and deployments can retune without a code change.
- **Amends ADR-030:** when the whole scope fits, `mode=overview` no longer stratifies one chunk per H1 — the complete document in document order is a superset of any stratified sample, so stratification is not a loss. Ranking, thresholds and `top_k` still govern every query that does not fit.
- **`rerank_score` is `None` on this path** (no CrossEncoder ran). Inventing a score would imply a ranking that does not exist; consumers (`extract_sources`, the SSE `sources` event, the `rag.query` dedupe) key on `content`/`source`/`section` and are unaffected.
- **Operational finding (recorded, not fixed here):** `ollama_context_window` is never sent to Ollama — `providers/ollama.py` posts to `/v1/chat/completions`, and `num_ctx` is silently ignored on that endpoint (verified live 2026-10-01 against `ornith-1.5:9b`: the compat endpoint accepted both `options.num_ctx=512` and top-level `num_ctx=512` unchanged, while native `/api/chat` enforced `options.num_ctx=512`). The effective window is the Ollama server's `OLLAMA_CONTEXT_LENGTH` (measured 32768), which currently matches the 32768 default. The two values must be kept equal — raising `ollama_context_window` alone would make this gate over-optimistic. Enforcing it per-request would require moving `generate`/`generate_stream` to native `/api/chat`, a separate provider-behavior change.
- **Consequences:** Cheap reads of small files get faster and more complete; behavior for oversized scopes is unchanged. `test_whole_file.py` covers the budget/cap/fallback gates and `test_tools.py::TestRagQuery` pins that a hit makes zero planner calls while a legacy double still reaches `retrieve_context`.

## ADR-034: Join loader pages before header splitting; make chunk storage replace-not-append

- **Status:** Accepted
- **Context:** A user asked for "all the lab names" in an 18-page lab report and got 1, 2, 3, 5, 6. ADR-033's whole-file return was working correctly (it returned every chunk); the loss was already in the database. Both loaders return one Document per page, and `chunk_documents` split each page independently. OpenDataLoader placed `# Lab 4: Network and Information Lab` as the **last line of page 10 with no body under it**; page 11 carried Lab 4's body but never repeated the heading. `MarkdownHeaderTextSplitter` only emits a chunk once it accumulates body lines beneath a heading, so that trailing heading produced **no chunk at all** — the string "Lab 4" was silently discarded at ingest, while its content was stored as 9 chunks with `H1=''`. 23 of 30 chunks had no `H1`; no spelling of "Lab 4" existed anywhere in `embeddings`. Section titles are the highest-signal text for both the CrossEncoder and the grounded answer (`_section_prefix`), so this silently dislabelled whole sections.
- **Decision:** (1) `chunk_documents` joins the loader's page Documents with `"\n\n"` **before** header splitting, so a heading attaches to the body it introduces regardless of page breaks. Verified on the real report: 30 chunks / 23 unlabelled / 0 "Lab 4" → 19 chunks / **0 unlabelled** / 6 Lab 4 chunks, and every chunk now carries an `H1`. No schema, config or dependency change; the header-path behaviour for single-page and headerless input is unchanged. (2) `store_chunks_and_embeddings` now `DELETE`s the file's rows in the same transaction before inserting, making re-processing idempotent. It was INSERT-only, so every retry of the documented recovery path (`POST /api/files/{id}/process`, ADR-005/PLAN B3) duplicated content, restarted `chunk_index` at 0 against stale rows, and inflated the ADR-033 size probe until the whole-file shortcut silently stopped firing for that file.
- **Operational note (not a code change):** on the host without egress, Docling cannot download `docling-layout-heron` and every ingest silently falls back to OpenDataLoader (`⚠️ Docling failed, fallback to OpenDataLoader: ConnectError`), which needs Java. Watch the backend log to know which loader actually produced a document.
- **Migration:** the fix only applies to newly parsed documents. **Every already-ingested file must be re-processed** (`POST /api/files/{file_id}/process`, or re-upload) or it keeps its orphaned headings. Re-processing is now safe to repeat.
- **Consequences:** Sections keep their titles across page boundaries, which improves reranking and grounding generally, not just for this report. Chunk counts drop for multi-page documents (finer page-local splits had been silently producing extra unlabelled chunks), so per-file `SUM(char_length)` falls and the ADR-033 shortcut fires more often. `test_ingest_chunking.py` pins heading survival across a page boundary, the no-empty-chunk guarantee, and delete-before-insert ordering.

---

## ADR-035: Segregate doc vs non-doc intents; route-aware ReAct (trace `c9b02eef`)

- **Status:** Accepted (2026-10-01)
- **Context:** "Plot India vs China GDP growth in a line graph" on an empty notebook failed the whole run: `{"status": "failed", "error": "no deterministic builder for intent plot_standalone — L3 ReAct required"}`. Four independent defects, none of them about plotting. (1) The router never saw the corpus (`engine.py` passed only request text + memory), so it could not tell "answer from the documents" from "answer from your own knowledge" and routed a parametric plot as a document plot. (2) ReAct was invoked with no knowledge of what the router had already decided, so a 3B model re-litigated "should I search?" on every iteration. (3) The empty-corpus refusal of `rag.query` was charged to `IdleGuard`: iteration 1 (a `plot.chart` shape hint) was idle 1, iteration 2 (`rag.query` refusal) was idle 2 → the loop broke at iteration 2 having executed **nothing**. (4) With zero executed steps the run surfaced the *routing* error text to the user, although the routing was correct. Separately, six of the fourteen intents had no builder at all (`report`, `convert_ambiguous`, `code`, `image`, `vision`, `plot_standalone`) and two advertised capabilities that cannot function: `image.generate` fails honest on every call because `ollama_image_model` is empty by default (`config.py:63`), and the vision agent is text-only with no image upload path (`files.py` allows `.pdf/.doc/.docx/.md/.txt`).
- **Decision:** (1) **Buckets as data.** `intents.py` gains `DOC_INTENTS` (answer lives in the notebook: `qa_single/compare_multi/summarize/summarize_plot/report/convert_one/convert_all/convert_ambiguous/quiz`), `NON_DOC_INTENTS` (`chat/knowledge_qa/plot_standalone/code`) and `REACT_ONLY_INTENTS` (`summarize_plot/plot_standalone`). `DETERMINISTIC_INTENTS` is now **derived** — `(DOC | NON_DOC) − REACT_ONLY` — so a new intent is deterministic by default and must be declared REACT_ONLY to earn a ReAct hop. Enum values are unchanged for existing intents (the SSE `route.intent` field stays wire-stable). (2) **Corpus-aware router.** `Router.route()` takes `notebook_context` and the prompt states the snapshot plus a tri-stated corpus line, with the menu split into labelled DOC-BASED / NON-DOC blocks and the decisive rule "if the request can be answered without the notebook, choose a NON-DOC intent". Chart precedence is now by data source: message/parametric numbers → `plot_standalone`, document data → `summarize_plot`. (3) **Missing intents implemented.** `knowledge_qa` (new intent, one `reasoning` step — the parametric home that stops `qa_single` absorbing general questions), `code` (one `coding` step), `report` (per-file `overview` shards → one `reasoning(answer)` writer → `doc.generate` whose section body is the writer's `{{id}}`, so ADR-027 report-grounding is structural), `convert_ambiguous` (one `clarification` step naming the ready files and the three formats). Unresolvable `convert_one`/`convert_all` (no format, no match, ambiguous match, nothing ready) now route to that counter-question instead of `None → ReAct` — one reasoning call instead of up to six iterations to ask the same question. (4) **Dead intents removed:** `image` and `vision` leave the taxonomy (tools/agents stay registered for ReAct). (5) **Route-aware ReAct.** `run_react` takes `route_intent` (threaded through `OrchestrationState.route_intent`) and the corpus state, and states both in a `Route:` block: the router's verdict, the corpus line, and — for plot intents — "your FIRST step must be a reasoning step recalling the figures, then plot.chart". (6) **Substitution over refusal.** On an empty/processing corpus a proposed `rag.query` is replaced by a general-knowledge `reasoning` step that *executes*, so its observation reaches the scratchpad and a repeat hits the existing seen/failed guards; the iteration span records `substituted_from`. (7) **Blocked ≠ idle.** `IdleGuard` gains `record_blocked()` with its own budget (`MAX_BLOCKED_TURNS=3`); an environment refusal no longer consumes the model's idle budget, so a shape hint plus a corpus block can no longer end a run. (8) **Last-resort answer.** A loop that ends with no successful step runs one `reasoning` step carrying the request verbatim (the `chat`/`knowledge_qa` shape, ADR-026's philosophy extended to ReAct); if that fails too, the run still raises the original `OrchestrationError`. (9) **Targeted `notebook.inspect`.** Prompt-only, and only when the corpus state is `unknown` (the inventory query failed) on a DOC intent — the one case where probing carries information the prompt does not already hold; charged to the block budget, not enforced forever.
- **Rationale for not mandating `notebook.inspect` first:** the snapshot is already in the ReAct prompt (`Notebook documents:`), so a blanket first-step inspect costs an iteration on every ReAct run to re-read what the prompt states — and returns an empty list precisely in the empty-notebook case that motivated the report. Restricting it to `unknown` keeps the ADR-027 freshness probe where it is informative.
- **Consequences:** 14 → 13 intents; L2 coverage 7/14 → 11/13, with the only ReAct residue being the two chart intents, now stated in code rather than a comment. A parametric request on an empty notebook no longer produces a routing-error failure. Unresolvable converts cost one reasoning call instead of a ReAct round trip. `qa_single` on an empty corpus now returns the same plan as `knowledge_qa` (single verbatim `reasoning` step) — identical output, one shared implementation. `corpus._snapshot_files` strips the snapshot's `N file(s):` prefix from parsed names, which was leaking into user-facing text. Breaks three tests that pinned the old fail-honest-with-routing-error behavior and one that pinned the `rag.query` refusal; the loop still fails fast in all of them (2 turns, no recall), so the honest-failure contract is preserved at the loop level and moved to the run level.
- **Amends:** ADR-029/ADR-032 (builder list, router prompt), ADR-030 (empty-corpus rule → substitution), ADR-031 (ReAct input contract).
- **Follow-up amendment (same trace, `plot.chart` never ran):** the ADR-035 fixes above turned the failed run into a successful one, but the second trace showed the chart itself still missing. Iterations 2 and 3 both proposed `plot.chart` with the right data and a wrong shape — `series_labels: ["India"]` plus a parallel `china_values` array — and were rejected twice on the same field, so the loop broke at iteration 3 with no chart and the run still reported `success` (an ASCII-art redraw from the reasoning observation stood in for the artifact). Three fixes. (a) **Shape by example, not prose.** `_PLOT_SERIES_EXAMPLE` — a literal, copyable two-series `plot.chart` call naming the exact failure ("There is no `series_labels` key and no per-entity array like `india_values`/`china_values`") — is embedded in both the ReAct prompt and the validation hint. The previous prose rule ("grouped comparisons use `series:[{label, values}]`") was not something the 3B model could map to JSON. (b) **Schema guard replaces the single-key special case.** Validation now rejects *any* key outside `{chart_type, labels, values, series, title}`. The old `series_labels`-only branch let `china_values` through: with `labels`+`values` present the tool would have accepted the call and silently charted India alone — a dropped series, i.e. the ADR-027 hallucination class in a new costume. (c) **A missing chart is reported.** When the routed intent is a chart intent and no `plot.chart` step succeeded, an `r-chart` FAILURE result is appended (no plan step — nothing executed), so the aggregator's partial-failure rule surfaces it and the run reports `partial` instead of `success`.
- **Also in that trace:** a non-final ReAct agent step was typed `expected_output_type="text"` and therefore SHOWN, so the scratchpad observation (the ASCII "plot") was concatenated into the user-visible summary ahead of the final synthesis. Non-terminal agent steps are now typed `observation` and hidden by the aggregator; the anti-blank fallback still surfaces one when it is the only output. `observation` joins `chunks`/`numbers` in the hidden set and in the validator's known-type vocabulary.
- **Second amendment — the tool contract moved home (`react_engine.py` was not the right place for it):** the fixes above put `plot.chart`'s shape in the *ReAct* module, which already had four hand-written copies of it (`_PLOT_KEYS`, `_TOOL_INPUT_HINTS`, `_validate_react_input`'s per-tool branches, plus the prompt prose) and the authoritative copy in `plot_chart.py`. They had already drifted — `series_labels` was a react-side special case the tool never knew about, and `china_values` passed validation entirely. The registry exists precisely so contracts are declared once and read by consumers; the validator already did this for `required` via `manifest()`, ReAct simply never asked. (1) `Tool` gains the contract: `input_schema` (declarative) + `validate_input` (cross-field overrides) + `required_for_model` (rules only an LLM must satisfy, e.g. plot.chart wants a title while the renderer tolerates its absence — trace affdbbd4) + `input_example` (one copyable call). (2) `tools/schema.py` adds a dependency-free JSON-Schema subset checker (`type`/`properties`/`required`/`enum`/`minItems`/`additionalProperties: false`/`items`) so a closed key set, a required field and an enum are *data*, not `if` statements — offline-first, no `jsonschema`. Every tool now declares `additionalProperties: false`, which is what makes an invented `china_values` a rejection instead of a silently dropped series. (3) `execute()` calls `validate_input` first, so a tool cannot ship rules its own execution ignores; the guards moved out of `execute()` bodies into `validate_input` overrides with their messages unchanged. (4) ReAct keeps the pre-flight check — for economics, not correctness: a failing step is retried twice inside `run_plan_graph`, so a malformed call costs three executions and three identical scratchpad errors versus one idle turn — but it is now `tools.get(executor).explain_invalid(...)`, a registry lookup, not a rule set. (5) The prompt's tool list is rendered from `input_example` via `_tool_examples`, so a tool's advertised shape cannot drift from its enforced one.
- **Engine-owned keys, discovered by the closed key set:** `run_plan_graph` injects `notebook_id` *and* `expected_output_type` into every resolved step input, so `tools/schema.py::ENGINE_INJECTED_KEYS` exempts both from required-field and unknown-key checks — the same exemption the plan validator already applied to `notebook_id`. `_normalize_react_input` now *moves* the `message`→`query`/`code` alias instead of copying it (leaving it behind is now litter, and would have been rejected). `rag.query` declares its `message` alias in the schema so Athena-era plans still validate.
- **Consequences of the second amendment:** `react_engine.py` loses ~120 lines of duplicated rules; adding a tool now means writing its contract once (schema + `validate_input` + example) and both the validator's menu and ReAct's hint follow automatically. Cross-step dataflow rules ("plot values must reference a numbers step") stay in `orchestration/validator.py` — they are placeholder-aware in a way a runtime input contract deliberately is not.

---

## ADR-036: doc.generate writes verbatim text files, not just reports

- **Status:** Accepted
- **Context:** UAT case 9 ("save the figures as scores.csv", "write me a quicksort script") has no legal tool call: `doc.generate` only rendered `title`+`sections` reports, `doc.convert` only converts uploads byte-for-byte, and `code.sandbox` runs code without persisting it. The report template is the wrong shape for CSV/JSON/code output.
- **Decision:** `doc.generate` gains a second path, exactly one per call: `content` + `format`/`filename` writes the text byte-for-byte (txt/csv/md/json/py/js/ts/html/css/sh/yaml/yml/xml allowlist — data/text only, no executables or archives). `filename` wins when present and must agree with `format`; the report path (`title`+`sections`) is unchanged and rejects verbatim keys. Delivery reuses the artifact pipeline via a generic `file_b64`/`filename`/`mime` shape. The plan validator checks the either/or contract through the tool itself (placeholder-tolerant: inputs carrying `{{id}}` still validate, execution re-checks after resolution).
- **Consequences:** Report builders/prompts unchanged; a new verbatim intent is future work — ReAct reaches the path via the tool example today. `artifacts.py` collects the generic file shape for any current or future producer.

---

## Historical (superseded, one line each)

- **ADR-002** (hybrid vector + graph RAG): superseded by ADR-007.
- **ADR-003** (on-premise OpenAI-compatible `LLM_URL`): superseded — Ollama-only via `OLLAMA_BASE_URL`.
- **ADR-006** (deferred Agentic RAG stub): superseded — agentic behavior lives in orchestration.
- **ADR-008–013** (merge mechanics: Ollama-only, `backend/app/` root, single messages table, rolling summary, SSE break, keep LangGraph): fulfilled during the merge; kept state is recorded above.
- **ADR-028** (bounded planner recall): superseded by ADR-032.
