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

## ADR-035: Remove coding/vision agents and code.sandbox/image.generate tools

- **Status:** Accepted
- **Context:** Audit showed all 3 agents + 7 tools were implemented with docs/prompts matching registries exactly — nothing phantom. But `coding`/`vision` agents and `code.sandbox`/`image.generate` tools were degraded in practice: sandbox needs a Docker daemon (absent on the office host), image generation needs an image model (`OLLAMA_IMAGE_MODEL` empty by default), vision is text-only with no image plumbing. They cost prompt surface, validator branches, provider surface, config keys, docs, and tests while only failing honestly.
- **Decision:** Delete `agents/coding.py`, `agents/vision.py`, `tools/code_sandbox.py`, `tools/image_generate.py`; unregister from both registries (1 agent + 5 tools remain). Remove `ModelProvider.generate_image` (+ `Ollama` override), `ollama_image_model` + `sandbox_*` settings, the artifacts `image_b64` branch, and all ReAct prompt/validation/normalization branches. Keep `CODE`/`IMAGE`/`VISION` router intents — they fall through to L3 ReAct, where the `reasoning` agent answers in text honestly. `CODE`-intent code questions still get code as text; image draws get an honest can't-do.
- **Consequences:** Smaller prompt, no dead branches, no inert env keys (`SANDBOX_*`, `OLLAMA_IMAGE_MODEL` removed from `.env.example`). Saved runs referencing removed executors replay with unknown-executor labels (display-only).

## ADR-036: Per-step token budgets (chat cheap, code generous)

- **Status:** Accepted
- **Context:** `DEFAULT_MAX_TOKENS=2048` was a single global output cap: every agent step paid the same ceiling. Coding steps inline whole files and emit test scripts (truncated at 2048), while chat steps are greetings that should never spend 2048.
- **Decision:** Builders tune output per intent via step input `max_tokens` (`chat` → `CHAT_MAX_TOKENS=1024`, `CODE` → `CODING_MAX_TOKENS=4096`); everything else rides `DEFAULT_MAX_TOKENS`. `plan_graph` forwards it onto `DelegationRequest.max_tokens` (non-int values dropped to the agent default); `reasoning` defaults to `default_max_tokens`, `coding` to `coding_max_tokens`. Budgets cap generated tokens only — they do not extend `OLLAMA_CONTEXT_WINDOW` (ADR-033 still governs the prompt side).
- **Consequences:** One cheap knob per task shape, tunable via `.env` without code changes. ReAct turns carry no explicit budget and fall back to agent defaults (coding stays generous there too).

## ADR-037: Coding agent returns as generate-only (no sandbox)

- **Status:** Accepted
- **Context:** ADR-035 removed `coding` with the sandbox because execution had no daemon to run on. The follow-up need is narrower: users upload code files and ask for test scripts — generation with the file inlined, presented in chat, no internal execution.
- **Decision:** Restore `agents/coding.py` as a prompt-only sibling of `reasoning` (code-specialized system prompt, same provider, generous `coding_max_tokens` budget). `CODE` is a deterministic builder intent: single `coding` step, file content read from disk and inlined — no tools, no placeholders, no execution. Code files (any language in `code_extensions`) bypass vector ingest entirely: stored on disk + marked `ready:code`, read as text on demand. No sandbox, no new tools; execution stays deferred.
- **Consequences:** 2 agents, 5 tools. Doc builders ignore code files (`_ready_files` is docs-only); code-only notebooks report empty corpus for retrieval. Saved runs predate the agent and are unaffected (replay is display-only).

## ADR-038: CODE becomes the generalist software intent; IMAGE/VISION removed

- **Status:** Accepted
- **Context:** `IMAGE`/`VISION` had no tools, no builders, and no executors — every such route ended in `plan_error → L3 ReAct`, where the model then had to render large outputs through `generate_structured` JSON (observed: a landing-page ask misrouted to `image` at 0.9, then failed JSON parsing on the 7KB page). Worse, `CODE` was framed as uploaded-file work only, so greenfield asks ("generate a landing page", no files) matched no intent well and `build_code` answered them with an "upload a file" clarification.
- **Decision:** Delete the `IMAGE`/`VISION` intents (router prompt is generated from `INTENT_DESCRIPTIONS`, so they leave the prompt automatically; stale `image`/`vision` values parse to `UNKNOWN` → ReAct). Generalize `CODE` to any software task in any language — greenfield generation (HTML/CSS/JS pages, apps, scripts) or uploaded-file work — with a router precedence rule (web/HTML/CSS/JS/UI → `code`). `build_code` splits the no-targets case: named-but-unresolvable file → clarification; no hint → greenfield `coding` step carrying the request alone.
- **Consequences:** Picture/photo/illustration asks now route to `UNKNOWN` → ReAct, which answers honestly in text (no image model exists). Greenfield code asks get a deterministic single `coding` step with the generous `coding_max_tokens` budget. Supersedes the ADR-035 "keep `IMAGE`/`VISION` intents" line.

---

## Historical (superseded, one line each)

- **ADR-002** (hybrid vector + graph RAG): superseded by ADR-007.
- **ADR-003** (on-premise OpenAI-compatible `LLM_URL`): superseded — Ollama-only via `OLLAMA_BASE_URL`.
- **ADR-006** (deferred Agentic RAG stub): superseded — agentic behavior lives in orchestration.
- **ADR-008–013** (merge mechanics: Ollama-only, `backend/app/` root, single messages table, rolling summary, SSE break, keep LangGraph): fulfilled during the merge; kept state is recorded above.
- **ADR-028** (bounded planner recall): superseded by ADR-032.
