# Architecture Decision Records (ADR)

Each entry records a decision with status, context, the decision, and consequences — stated as the project actually is today. Superseded or deferred items are noted explicitly.

---

## ADR-001: FastAPI + Python backend
- **Status:** Accepted
- **Context:** A backend that must integrate heavy ML libraries (Docling, sentence-transformers/BGE) and handle long-running streaming responses.
- **Decision:** Use FastAPI on Python 3.11+, with an async lifespan that loads the ML models once at startup.
- **Consequences:** Fast async/streaming support (SSE), easy integration with the Python ML ecosystem, auto-generated OpenAPI docs. The lifespan loads models eagerly, so startup fails fast if local weights are unavailable.

## ADR-002: Hybrid vector + graph RAG
- **Status:** Superseded by ADR-007
- **Context:** Standard vector search is insufficient for the complex, cross-document relationships and precise domain terminology in physics R&D.
- **Decision:** Combine PostgreSQL + **pgvector** (vector cosine + full-text) with a **Neo4j** knowledge graph (chunk `NEXT`/`MENTIONS` edges and `Entity` nodes). Retrieval is orchestrated by `GraphRAG` (the active path); `VectorRAG` (vector + full-text with Reciprocal Rank Fusion) remains for the legacy non-stream endpoint.
- **Consequences:** Deeper context across technical documents, but higher operational complexity (two data stores to keep in sync) and a slower, LLM-in-the-loop retrieval path. See Known Issues #2 for a current bug in the `VectorRAG` path.

## ADR-003: Offline / on-premise model execution over OpenAI-compatible HTTP
- **Status:** Accepted
- **Context:** Internal R&D data must remain confidential and air-gapped.
- **Decision:** Keep all models local/on-premise. Embeddings and reranking load from local weights (`D:\models\...`, hardcoded in `config.py`); the chat/rewrite/entity LLM is reached over an internal OpenAI-compatible endpoint (`LLM_URL`, model `qwen2.5-coder-14b`).
- **Consequences:** Zero external cloud dependencies. Trade-offs: model paths are non-portable (Windows-specific), and the exact LLM endpoint is environment-dependent. An earlier plan to use Ollama (`OLLAMA_URL`) is **not** in the live code path.

## ADR-004: Docling → Markdown, header-based chunking
- **Status:** Accepted
- **Context:** Physics documents (equations, tables, sectioned papers) need parsing that preserves structure and meaningful boundaries.
- **Decision:** Parse with **Docling** to Markdown, then chunk on Markdown headers (`#/##/###` → H1/H2/H3) via `MarkdownHeaderTextSplitter`. Semantic chunking exists in code but is disabled.
- **Consequences:** Section-aware metadata on chunks (`source`, H1/H2/H3) that feeds retrieval and source display. Complex equations/tables are only as good as Docling's output; see `docs/PLAN.md` Phase 2.

## ADR-005: Background-task ingestion pipeline
- **Status:** Accepted
- **Context:** Document ingestion (parsing, embedding) is slow and must not block the HTTP request.
- **Decision:** `POST /api/files/{file_id}/process` enqueues `run_rag_pipeline` as a FastAPI `BackgroundTasks` job; the frontend polls file status until `ready`.
- **Consequences:** Responsive UX, but no progress reporting or retry beyond a final `ready`/`error` status, and no durable job queue — a restart mid-ingestion leaves a file stuck in `processing`.

## ADR-006 (Deferred): Agentic RAG
- **Status:** Deferred / not implemented
- **Context:** An intended next step was an agentic retrieval layer choosing strategies per query.
- **Decision:** The `AgenticRAG` stub (`retrieve_context` was `pass`) was removed together with the graph RAG stack (see ADR-007). No agentic layer exists.
- **Consequences:** Do not treat agentic RAG as working. The active retrieval is `VectorRAG`. Revisit when the pipeline is stable and the Known Issues are resolved.

## ADR-007: Remove graph RAG; single vector + full-text retrieval path
- **Status:** Accepted (supersedes ADR-002)
- **Context:** The Neo4j knowledge graph (LLM entity extraction during ingestion, `store_graph`, hybrid graph traversal in `GraphRAG`) required a second datastore to operate and keep in sync, a mandatory Neo4j service, and a slow LLM-in-the-loop retrieval path. `VectorRAG` (pgvector + full-text with Reciprocal Rank Fusion + BGE reranking) already served the non-stream endpoint and produces the same context shape.
- **Decision:** Remove the graph RAG stack entirely: `rag/graph_rag.py`, `rag/agentic_rag.py`, `core/prompts.py`, document-type detection, entity extraction, `store_graph`, the Neo4j driver in `core/db.py`, graph cleanup in the delete endpoints, the `neo4j` dependency, and the `NEO4J_*` env vars. `VectorRAG` is the single retrieval path for both prompt endpoints; ingestion is load → chunk → embed → store.
- **Consequences:** One datastore (PostgreSQL), faster ingestion (no per-chunk LLM extraction), simpler deployment and onboarding. Existing data in the Neo4j database is orphaned and can be dropped. Graph retrieval can be reintroduced later via a new ADR if cross-document traversal becomes a requirement.
