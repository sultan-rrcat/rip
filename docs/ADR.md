# Architecture Decision Records (ADR)

> Merge mode: `docs/MERGE_PLAN.md` is authoritative for Athena→RIP work. This file keeps history + merge decisions only.

Each entry records a decision with status, context, the decision, and consequences.

---

## ADR-001: FastAPI + Python backend
- **Status:** Accepted
- **Context:** A backend that must integrate heavy ML libraries (Docling, sentence-transformers/BGE) and handle long-running streaming responses.
- **Decision:** Use FastAPI on Python 3.11+, with an async lifespan that loads the ML models once at startup.
- **Consequences:** Fast async/streaming support (SSE), easy integration with the Python ML ecosystem, auto-generated OpenAPI docs. The lifespan loads models eagerly, so startup fails fast if local weights are unavailable.

## ADR-002: Hybrid vector + graph RAG
- **Status:** Superseded by ADR-007, archived with merge (see MERGE_PLAN Appendix A)
- **Context:** Standard vector search is insufficient for the complex, cross-document relationships and precise domain terminology in physics R&D.
- **Decision:** Combine PostgreSQL + **pgvector** (vector cosine + full-text) with a **Neo4j** knowledge graph (chunk `NEXT`/`MENTIONS` edges and `Entity` nodes). Retrieval is orchestrated by `GraphRAG` (the active path); `VectorRAG` (vector + full-text with Reciprocal Rank Fusion) remains for the legacy non-stream endpoint.
- **Consequences:** Deeper context across technical documents, but higher operational complexity (two data stores to keep in sync) and a slower, LLM-in-the-loop retrieval path. (Pre-merge note, retained for history.)

## ADR-003: Offline / on-premise model execution over OpenAI-compatible HTTP
- **Status:** Superseded by merge (see MERGE_PLAN §Design Decisions: Ollama-only, `ollama_default_model=qwen2.5:14b`)
- **Context:** Internal R&D data must remain confidential and air-gapped.
- **Decision:** Keep all models local/on-premise. Embeddings and reranking load from local weights (`D:\models\...`, hardcoded in `config.py`); the chat/rewrite/entity LLM is reached over an internal OpenAI-compatible endpoint (`LLM_URL`, model `qwen2.5-coder-14b`).
- **Consequences:** Zero external cloud dependencies. Trade-offs: model paths are non-portable (Windows-specific), and the exact LLM endpoint is environment-dependent. An earlier plan to use Ollama (`OLLAMA_URL`) is **not** in the live code path.

## ADR-004: Docling → Markdown, header-based chunking
- **Status:** Accepted
- **Context:** Physics documents (equations, tables, sectioned papers) need parsing that preserves structure and meaningful boundaries.
- **Decision:** Parse with **Docling** to Markdown, then chunk on Markdown headers (`#/##/###` → H1/H2/H3) via `MarkdownHeaderTextSplitter`. Semantic chunking exists in code but is disabled.
- **Consequences:** Section-aware metadata on chunks (`source`, H1/H2/H3) that feeds retrieval and source display. Complex equations/tables are only as good as Docling's output; see MERGE_PLAN Appendix A.

## ADR-005: Background-task ingestion pipeline
- **Status:** Accepted
- **Context:** Document ingestion (parsing, embedding) is slow and must not block the HTTP request.
- **Decision:** `POST /api/files/{file_id}/process` enqueues `run_rag_pipeline` as a FastAPI `BackgroundTasks` job; the frontend polls file status until `ready`.
- **Consequences:** Responsive UX, but no progress reporting or retry beyond a final `ready`/`error` status, and no durable job queue — a restart mid-ingestion leaves a file stuck in `processing`.

## ADR-006 (Deferred): Agentic RAG
- **Status:** Superseded by merge — agentic layer returns via Athena orchestration (see MERGE_PLAN)
- **Context:** An intended next step was an agentic retrieval layer choosing strategies per query.
- **Decision:** The `AgenticRAG` stub (`retrieve_context` was `pass`) was removed together with the graph RAG stack (see ADR-007). No agentic layer exists.
- **Consequences:** Do not treat agentic RAG as working pre-merge. The active retrieval is `VectorRAG`; agentic layer returns via merge orchestration.

## ADR-007: Remove graph RAG; single vector + full-text retrieval path
- **Status:** Accepted (supersedes ADR-002)
- **Context:** The Neo4j knowledge graph (LLM entity extraction during ingestion, `store_graph`, hybrid graph traversal in `GraphRAG`) required a second datastore to operate and keep in sync, a mandatory Neo4j service, and a slow LLM-in-the-loop retrieval path. `VectorRAG` (pgvector + full-text with Reciprocal Rank Fusion + BGE reranking) already served the non-stream endpoint and produces the same context shape.
- **Decision:** Remove the graph RAG stack entirely: `rag/graph_rag.py`, `rag/agentic_rag.py`, `core/prompts.py`, document-type detection, entity extraction, `store_graph`, the Neo4j driver in `core/db.py`, graph cleanup in the delete endpoints, the `neo4j` dependency, and the `NEO4J_*` env vars. `VectorRAG` is the single retrieval path for both prompt endpoints; ingestion is load → chunk → embed → store.
- **Consequences:** One datastore (PostgreSQL), faster ingestion (no per-chunk LLM extraction), simpler deployment and onboarding. Existing data in the Neo4j database is orphaned and can be dropped. Graph retrieval can be reintroduced later via a new ADR if cross-document traversal becomes a requirement.

---

## ADR-014: Postgres-backed runs with SSE replay

- **Status:** Accepted
- **Context:** Runs must survive page refreshes and notebook switches. Only the stop button can terminate a run. The original design specified in-memory-only runs, but a page refresh kills the SSE connection and the run becomes orphaned.
- **Decision:** Persist run state and structural SSE events to Postgres (`runs` + `run_events` tables). Per-token `delta` events are live-only and not persisted (Q35). The SSE endpoint replays persisted events on reconnect; frontend deduplicates by `seq`. Redis stays optional for queue/cache.
- **Consequences:** Runs survive page refreshes. Reconnect replays plan/steps/final text without delta flood. The stop button sends `POST /v1/runs/{id}/cancel` which sets `status=cancelled`; the worker checks this before each step.

## ADR-015: Remove conversations table

- **Status:** Accepted
- **Context:** The original design had a separate `conversations` table with a 1:1 relationship to notebooks. This added unnecessary complexity: a redundant table, a nullable FK on messages, and confusing dual ownership (notebook_id + conversation_id).
- **Decision:** One notebook = one conversation. Remove the `conversations` table entirely. Internal memory fields (`conversation_summary`, `summary_message_count`) live on the `notebooks` table (Q38). Messages are linked to notebooks via `notebook_id` only; the `conversation_id` column is dropped.
- **Consequences:** Simpler data model. No join needed to get conversation state — it's on the notebook row. Pre-merge messages with `conversation_id` set are unaffected (column dropped, `notebook_id` still valid).

## ADR-016: Context-window-based summary

- **Status:** Accepted
- **Context:** The original design triggered summary after >10 turns. This is a rough heuristic — it doesn't account for varying message lengths or the actual model context window. A 32k-context model might fill up in 5 long turns or 20 short ones.
- **Decision:** Summary triggered when accumulated message tokens reach ~70% of the model context window. The summary is generated by Ollama and stored in `notebooks.conversation_summary` (internal, not user-visible; distinct from SSE `summary` event = final answer). Recent messages are kept verbatim; old messages are compressed into the summary.
- **Consequences:** More accurate, model-aware context management. The `summary_threshold_pct` config (default 0.7) controls the trigger. The `memory_window_size` config remains as an advisory cap on verbatim messages, but the real constraint is the token budget.

---

## Merge ADRs (details in MERGE_PLAN.md, logged here for status)

- **ADR-008 Ollama-only:** Accepted. No Gemini/llama-server; rewrite 5 coupled files to `ollama_default_model`.
- **ADR-009 Backend root `backend/app/`:** Accepted. Single `from app.*` root; RIP flat files move under it.
- **ADR-010 Single messages table:** Superseded by ADR-015. `conversation_id` column dropped; notebook = conversation.
- **ADR-011 Rolling summary kept:** Superseded by ADR-016. Summary by context window (~70%), not turn count.
- **ADR-012 Break SSE/REST:** Accepted. Delete `/api/prompt[/stream]`, serve `/v1/runs` + new SSE; keep `/api/health` alias.
- **ADR-013 Keep LangGraph:** Accepted. `engine.py` + `plan_graph.py` stay; plugins/admin-console/tracing removed.
- **ADR-014 Postgres-backed runs:** Accepted. Runs persist to Postgres (`runs` + `run_events`); survive page refresh; full SSE replay on reconnect; only stop button terminates.
- **ADR-015 Remove conversations table:** Accepted. One notebook = one conversation. Internal memory on `notebooks.conversation_summary`. Messages linked by `notebook_id` only.
- **ADR-016 Context-window-based summary:** Accepted. Summary triggered at ~70% of model context window. Stored in `conversation_summary` (not SSE `summary` event).
- **ADR-017 Direct capability inheritance & VectorRAG singleton:** Accepted. Providers/agents/tools inherit directly from base classes (dropping plugin scaffolding); `rag.query` reuses application lifespan `VectorRAG` singleton.
- **ADR-018 Drop query rewrite:** Accepted. Delete `rewritter.py` + legacy `services/llm.py`.
- **ADR-019 File-based artifacts:** Accepted. Files on disk; SSE download URLs.
- **ADR-020 Structural SSE persistence:** Accepted. No delta replay from Postgres.
- **ADR-021 Run worker contract:** Accepted. Memory load/persist; never write messages; always orchestrate.
- **ADR-022 Sources SSE event:** Accepted. Citations on `rag.query` complete.
- **ADR-023 Deterministic aggregator:** Accepted. Q36 selection rules; no LLM synthesis.
- **ADR-024 Admin health stub:** Accepted. Health only; no PluginManager endpoints.

---

## ADR-017: Direct capability inheritance and VectorRAG singleton

- **Status:** Accepted
- **Context:** Athena's agent, tool, and provider classes inherited from dynamic plugin wrappers (`ProviderPlugin`, `AgentPlugin`, `ToolPlugin` in `app.plugins.api`). With the plugin system removed, dynamic discovery is replaced by static composition. Additionally, re-instantiating `VectorRAG()` on every `rag.query` tool call would repeatedly reload heavy PyTorch model weights (BGE-M3 and reranker).
- **Decision:**
  1. Inherit directly from `ModelProvider`, `Agent`, and `Tool` base classes; drop `app.plugins.api` imports and wrappers.
  2. Provide explicit factory functions (`get_default_agent_registry`, `get_default_tool_registry`) in registries.
  3. In `rag_query.py`, reuse the existing `VectorRAG` singleton instantiated at application lifespan (`app.state.rag` via `get_rag()`).
  4. Port core utilities (`classutils.py`, `constants.py`), provider streaming helper (`streaming.py`), and artifact delivery (`artifacts.py`).
- **Consequences:** Eliminates plugin scaffolding, avoids PyTorch model reloading spikes per query, and ensures all imports resolve cleanly without dynamic discovery.

---

## ADR-018: Drop query rewrite

- **Status:** Accepted
- **Context:** Pre-merge RIP used `services/rewritter.py` + legacy `services/llm.py` to rewrite queries before RAG retrieval. Orchestration replaces the direct prompt path; rewrite added LLM cost and depended on forbidden `LLM_URL`.
- **Decision:** Delete `rewritter.py` and `services/llm.py`. No query rewrite in v1; the Planner + `rag.query` tool handle retrieval intent.
- **Consequences:** One fewer LLM call per turn. Planner must produce good search queries in plan steps.

## ADR-019: File-based artifacts

- **Status:** Accepted
- **Context:** Athena delivered artifacts as inline base64 in sync responses. RIP merge needs durable downloads scoped to notebooks.
- **Decision:** Write tool outputs to `{upload_dir}/{notebook_id}/artifacts/{artifact_id}/{filename}`. SSE `artifacts` event carries `{artifact_id, kind, filename, url}` download links.
- **Consequences:** Artifacts survive page refresh. Requires static file serving or download route under `/api/`.

## ADR-020: Structural SSE persistence (no delta replay)

- **Status:** Accepted
- **Context:** Persisting every token `delta` to Postgres would bloat `run_events` and flood reconnect clients.
- **Decision:** Persist structural events + final text only. `delta` is live-streamed to connected clients but not written to `run_events`. Reconnect uses `step_completed`/`summary` for text state.
- **Consequences:** Smaller DB footprint. Frontend must not expect delta replay after refresh.

## ADR-021: Run worker contract

- **Status:** Accepted
- **Context:** Athena's `runs/manager.py` wrote messages, used complexity routing, and mixed in-memory buffers with SQLite. Merge forbids backend message writes and requires notebook-scoped memory.
- **Decision:** Rewrite `_worker`: always orchestrate; load `conversation_summary` + messages → `build_memory_context()` → pass `context=`; persist updated summary to notebooks; emit `sources` on `rag.query`; never INSERT into `messages`.
- **Consequences:** Clear ownership boundary (frontend = messages, backend = runs + internal memory).

## ADR-022: Sources SSE event

- **Status:** Accepted
- **Context:** Pre-merge RIP sent citations via deprecated `/api/prompt/stream` `{type:"sources"}`. New SSE protocol had no citation path.
- **Decision:** Emit `{type:"sources", sources:[{source, section}]}` when `rag.query` completes; persist to `run_events`; frontend saves on assistant message.
- **Consequences:** Source citations preserved in merged UI without a separate RAG pre-call.

## ADR-023: Deterministic aggregator rules

- **Status:** Accepted
- **Context:** Athena's aggregator used LLM synthesis for multi-step plans. Merge removes LLM aggregation to cut cost and latency.
- **Decision:** One successful step → its output. Multiple successes → labeled concatenation. Clarification step → verbatim output. All failed → join error strings. No LLM call.
- **Consequences:** Predictable, testable aggregation. Less polish on multi-step synthesis.

## ADR-024: Admin health stub

- **Status:** Accepted
- **Context:** Athena admin endpoints depended on PluginManager for `/plugins` and `/reload`. Plugin system is removed.
- **Decision:** Keep `GET /v1/admin/health` only — returns static registry health from factory-built registries. Drop `/plugins` and `/reload` from v1.
- **Consequences:** No runtime plugin reload. Simpler admin surface.
