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

- **Status:** Accepted
- **Context:** Tool outputs must survive refresh as downloadable files scoped to notebooks.
- **Decision:** Write to `{upload_dir}/{notebook_id}/artifacts/{artifact_id}/{filename}`; SSE `artifacts` carries download URLs, never inline base64.
- **Consequences:** Durable downloads; requires a download route under `/api/`.

## ADR-020: Structural SSE persistence (no delta replay)

- **Status:** Accepted
- **Context:** Persisting per-token `delta` frames would bloat `run_events` and flood reconnects.
- **Decision:** Persist structural events + final text only. Reconnect rebuilds text from `step_completed`/`summary`.
- **Consequences:** Small DB footprint; frontend must never expect delta replay.

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

- **Status:** Accepted
- **Context:** LLM synthesis per run costs latency/money and is hard to test.
- **Decision:** 1 success → verbatim; N successes → labeled concatenation; clarification → verbatim; all-failed → joined errors.
- **Consequences:** Predictable and testable; less polish on multi-step synthesis.

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

---

## Historical (superseded, one line each)

- **ADR-002** (hybrid vector + graph RAG): superseded by ADR-007.
- **ADR-003** (on-premise OpenAI-compatible `LLM_URL`): superseded — Ollama-only via `OLLAMA_BASE_URL`.
- **ADR-006** (deferred Agentic RAG stub): superseded — agentic behavior lives in orchestration.
- **ADR-008–013** (merge mechanics: Ollama-only, `backend/app/` root, single messages table, rolling summary, SSE break, keep LangGraph): fulfilled during the merge; kept state is recorded above.
