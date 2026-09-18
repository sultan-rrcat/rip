# Architecture Decision Records (ADR)

Active decisions first; superseded merge-era history is collapsed at the bottom. Full merge rationale lives in `archive/MERGE_PLAN.md`.

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

- **Status:** Accepted (experiment — measure retry conversion in traces)
- **Context:** The deterministic gates (ADR-023/027) turn planner mistakes into honest failures, but the planner (`qwen2.5:14b`) repeats the same malformation across runs (garbled `",{{2}}"` plot values 3/3 despite explicit prompt rules) — each failure previously cost a full run with nothing learned. The merge-era "no replanning" rule explicitly no longer holds.
- **Decision:** One recall = two planner outputs max per run. A rejected plan replans with short validation feedback (rejected plan excerpt + exact validator message + one-line fix); a partial/failed aggregation replans with execution feedback (failed step ids + errors + prior plan, failed outputs only). Guards: clarifications never replan; cancellations suppress recall; second failure surfaces honestly; ADR-026 trivial repair sits outside the attempt budget; no resumption — retries re-execute fully. Feedback rides a `RETRY FEEDBACK` block appended after the planner examples plus an additive `attempt` field on the SSE `plan` event (second `plan` resets the frontend accumulators; attempt-1 artifact files orphan on disk).
- **Consequences:** Failure-path planning latency roughly doubles (~40–50s per plan call on the office model); execution retries re-spend RAG + step LLMs; success path unchanged. If trace-measured retry conversion stays low, stop tuning text and change the model or decoding instead.

## ADR-029: Layered planning (router + deterministic builders + ReAct fallback)

- **Status:** Accepted (2026-09-17, trace `ecd93eb4`)
- **Context:** The planner prompt (`planner.py`, ~243 lines + full manifests + Rules 1-9 + Examples A-F) grows with every hardening rule, and the single-shot DAG format forces the model to solve intent classification + DAG shape + placeholder wiring in one call. Observed live: a compare-two-reports request produced two good `rag.query` steps plus a reasoning step that named "step 1/2" in prose with no `{{1}} {{2}}`, ran in parallel with retrieval, and asked the user to re-upload. Appending more prompt rules does not scale.
- **Decision:** Layer the planning path, strictly additive; the existing mega-prompt is untouched as L3 fallback:
  1. L0 deterministic fast-path (`intents.classify_fast_path`): greetings/empty → single `reasoning` step, no LLM.
  2. L1 intent router (`router.py`): one tiny `generate_structured` call (`{intent, queries[≤3], confidence}`); `<0.6` → `unknown`.
  3. L2a deterministic builders (`builders.py`): fixed DAGs for `chat/qa_single/compare_multi` — wiring set by construction. L2b specialist micro-prompts stay future work; unknown intents fall to L3.
  4. L3 mega-prompt unchanged, incl. ADR-028 one-recall budget; layered routing runs on the first attempt only so retries stay pure mega-prompt.
  5. Validator gains `_check_prose_grounding`: prose step-references without `{{id}}` are rejected, but only when `rag.query` chunks siblings exist (no false positives on benign prose).
  6. L4 ReAct fallback (`react.py`): when plan recall is exhausted, a thought → action → observation loop (max 6 iterations, idle cap 2, cooperative cancel) answers one step at a time with observations inlined — no placeholders ever. Aggregates through the standard deterministic Aggregator; failure stays honest (`OrchestrationError` with the original plan error).
- **Consequences:** Happy path costs one extra cheap call (router) or zero (fast-path/deterministic hit); worst path is bounded (router + 2 plans + ≤6 single steps). New capabilities should land as intents + builders, not prompt appendices. `test_layered.py` pins the chain; recall-test queues carry a router-miss head.
- **Observability amendment (Option A, trace-only):** the router emits a `router` span as a sibling of `plan` under `run` (explicit `run_ctx` parenting; no graph/state change), `plan` output carries `layer/intent/routed_by/confidence`, and the ReAct fallback traces as `react → react:iter-N → step:rN` with fixed `parent_span_ctx` threading. The SSE `plan` event carries additive `route: {intent, routed_by, confidence}`.

---

## Historical (superseded, one line each)

- **ADR-002** (hybrid vector + graph RAG): superseded by ADR-007.
- **ADR-003** (on-premise OpenAI-compatible `LLM_URL`): superseded — Ollama-only via `OLLAMA_BASE_URL`.
- **ADR-006** (deferred Agentic RAG stub): superseded — agentic behavior lives in orchestration.
- **ADR-008–013** (merge mechanics: Ollama-only, `backend/app/` root, single messages table, rolling summary, SSE break, keep LangGraph): fulfilled during the merge; kept state is recorded above.
