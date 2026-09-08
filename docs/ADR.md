# Architecture Decision Records (ADR)

Each entry records a decision with status, context, the decision, and consequences — stated as the project actually is today. Superseded or deferred items are noted explicitly.

---

## ADR-001: FastAPI + Python backend
- **Status:** Accepted
- **Context:** A backend that must integrate heavy ML libraries (Docling, sentence-transformers/BGE, graph drivers) and handle long-running streaming responses.
- **Decision:** Use FastAPI on Python 3.11+, with an async lifespan that loads the ML models and a Neo4j driver once at startup.
- **Consequences:** Fast async/streaming support (SSE), easy integration with the Python ML ecosystem, auto-generated OpenAPI docs. The lifespan loads models eagerly, so startup fails fast if local weights or Neo4j are unavailable.

## ADR-002: Hybrid vector + graph RAG
- **Status:** Accepted
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
- **Context:** Document ingestion (parsing, embedding, LLM entity extraction, graph write) is slow and must not block the HTTP request.
- **Decision:** `POST /api/files/{file_id}/process` enqueues `run_rag_pipeline` as a FastAPI `BackgroundTasks` job; the frontend polls file status until `ready`.
- **Consequences:** Responsive UX, but no progress reporting or retry beyond a final `ready`/`error` status, and no durable job queue — a restart mid-ingestion leaves a file stuck in `processing`.

## ADR-006 (Deferred): Agentic RAG
- **Status:** Deferred / not implemented
- **Context:** An intended next step was an agentic retrieval layer choosing strategies per query.
- **Decision:** `AgenticRAG` exists only as a stub (`retrieve_context` is `pass`) and is not wired into the app.
- **Consequences:** Do not treat agentic RAG as working. The active retrieval is `GraphRAG`. Revisit when the current pipeline is stable and the Known Issues are resolved.
