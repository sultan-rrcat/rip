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
- **Consequences:** Section-aware metadata on chunks (`source`, H1/H2/H3) that feeds retrieval and source display. Complex equations/tables are only as good as Docling's output; see MERGE_PLAN Appendix A / Phase 2.

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
- **Decision:** Persist run state and events to Postgres (`runs` + `run_events` tables). The SSE endpoint replays all events for a run on reconnect (full replay, frontend deduplicates by event type + step_id). This adds 2 SQL tables but no new infrastructure (Postgres is already in the stack). Redis was considered but deferred to Phase 2 to avoid adding Day-1 infra risk.
- **Consequences:** Runs survive page refreshes. Frontend can reconnect and see the full run history. The stop button sends `POST /v1/runs/{id}/cancel` which sets `status=cancelled`; the worker checks this before each step.

## ADR-015: Remove conversations table

- **Status:** Accepted
- **Context:** The original design had a separate `conversations` table with a 1:1 relationship to notebooks. This added unnecessary complexity: a redundant table, a nullable FK on messages, and confusing dual ownership (notebook_id + conversation_id).
- **Decision:** One notebook = one conversation. Remove the `conversations` table entirely. Summary fields (`summary`, `summary_message_count`) move to the `notebooks` table. Messages are linked to notebooks via `notebook_id` only; the `conversation_id` column is dropped.
- **Consequences:** Simpler data model. No join needed to get conversation state — it's on the notebook row. Pre-merge messages with `conversation_id` set are unaffected (column dropped, `notebook_id` still valid).

## ADR-016: Context-window-based summary

- **Status:** Accepted
- **Context:** The original design triggered summary after >10 turns. This is a rough heuristic — it doesn't account for varying message lengths or the actual model context window. A 32k-context model might fill up in 5 long turns or 20 short ones.
- **Decision:** Summary triggered when accumulated message tokens reach ~70% of the model context window. The summary is generated by Ollama and stored on the `notebooks` table (internal, not user-visible). Recent messages are kept verbatim; old messages are compressed into the summary.
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
- **ADR-015 Remove conversations table:** Accepted. One notebook = one conversation. Summary stored on `notebooks` table. Messages linked by `notebook_id` only; `conversation_id` column dropped.
- **ADR-016 Context-window-based summary:** Accepted. Summary triggered at ~70% of model context window, not turn count. More accurate, model-aware.
