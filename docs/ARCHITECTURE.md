# Architecture: RAG Chatbot Assistant (RIP prototype)

This document describes the system **as it is implemented** in this repository. Where the code diverges from the original design intent, that is recorded under [Known Issues & Deviations](#known-issues--deviations) rather than being silently edited away.

---

## 1. Vision

An offline-first, NotebookLM-style RAG assistant for an internal physics R&D organization. Data privacy and on-premise control are the driving constraints: no telemetry, no external SaaS, all models reachable only inside the local network.

---

## 2. System topology

Decoupled client–server over HTTP. Browser → FastAPI → PostgreSQL (pgvector) + local LLM endpoint.

```
┌─────────────────────────┐   HTTP/REST + SSE   ┌──────────────────────────┐
│      React frontend     │ ──────────────────> │       FastAPI backend    │
│ (Vite · Tailwind · MUI) │                     │ (Python 3.11, async)      │
└─────────────────────────┘                     └────────────┬─────────────┘
                                                             │
                  ┌──────────────────────────────────────────┼───────────────────────────────┐
                  ▼                                          ▼                               ▼
    ┌──────────────────────────┐             ┌─────────────────────────────────┐
    │ PostgreSQL + pgvector    │             │  OpenAI-compatible LLM endpoint │
    │  embeddings_test (1024d) │             │  /v1/chat/completions           │
    │  notebooks · files ·     │             │  model: qwen2.5-coder-14b       │
    │  messages                │             └─────────────────────────────────┘
    └──────────────────────────┘
```

Everything (Docling parsing, BGE-M3 embeddings, BGE reranker, chat LLM) runs locally or against the internal `LLM_URL`.

---

## 3. Backend components (`backend/`)

### `app.py` — application entry
- Mounts routers `notebooks`, `files`, `messages`, `llm` (`app.py:38-41`).
- Async `lifespan` startup constructs `VectorRAG()` (loads BGE-M3 embeddings + BGE reranker from `config.py` paths) (`app.py:18-27`).
- `GET /api/health` → `{"status": "ok"}`.
- CORS allows all origins (`allow_origins=["*"]`).

### `config.py` — configuration
- `UPLOAD_DIR`, hardcoded model paths (`D:\models\...`), and `LLM_URL = os.getenv("LLM_URL")`.
- Dead entries kept for reference: `ALL_MINILM_L6_V2_MODEL_PATH`, `OLLAMA_URL`, `OLLAMA_EXTRACTION_MODEL` (no live code path uses them).

### `core/`
- `db.py` — `pg_connection()` (sync `psycopg2` context manager). Reads the `DB_*` env vars.
- `dependencies.py` — FastAPI dep: `get_rag` (returns `app.state.rag`, a `VectorRAG`).
- `logging.py` — idempotent `setup_logging()` writing to `backend/logs/app.log` and stdout.

### `rag/` — retrieval engine
| File | Role |
|---|---|
| `pipeline.py` | `RagPipeline` — ingestion primitives (load → chunk → embed → store). Base for the RAG classes. |
| `vector_rag.py` | `VectorRAG(RagPipeline)` — **the active retrieval orchestrator**: vector + full-text search with Reciprocal Rank Fusion and BGE reranking (used by both prompt endpoints). |
| `base.py` | `BaseRAG` ABC — currently unused by the concrete classes. |

### `routes/` — HTTP layer
Thin handlers over `core.db` / `core.dependencies`, with response shaping. Full table below.

### `services/` — business logic
| File | Purpose |
|---|---|
| `file_processor.py` | `run_rag_pipeline()` — async background ingestion (docling → chunk → embed → store). |
| `llm.py` | `ask_qwen` / `stream_qwen` — httpx calls to `{LLM_URL}/v1/chat/completions`, model `qwen2.5-coder-14b`. |
| `rewritter.py` | Conversation-aware query rewriting (last 6 messages), falls back to original prompt. |
| `chat.py` | `format_context_for_llm` (numbered `[i (source)]` blocks) and `extract_sources` (deduped). |

---

## 4. Frontend components (`frontend/src/`)

- **Entry / routing** — `main.jsx` → `App.jsx`: `BrowserRouter` with `/` (Home) and `/notebook/:notebook_id` (Notebook).
- **Pages** — `Home.jsx` renders the notebook gallery; `Notebook.jsx` composes the hooks and layout.
- **Mounted workspace UI** — `LeftSidebar.jsx` (Knowledge Base: file list with statuses + upload), `ChatArea.jsx` (markdown/code rendering), `Footer.jsx` (input + send).
- **State** — local `useState` via three hooks in `hooks/notebooks/`: `useNotebook` (name load/rename), `useMessages` (load, optimistic send, SSE streaming, persist), `useFiles` (list, 2s processing-poll, upload, delete). No Redux/Context store.
- **API clients** — `services/notebooks.js`, `messages.js`, `files.js`, `llm.js`; base URL `http://localhost:8000` hardcoded in `src/config.js`.
- **Styling** — Tailwind v4 (`@import "tailwindcss"` in `index.css`) + MUI v7 components/icons on the **default** MUI theme (no `ThemeProvider`/`createTheme`). Inter font via `@fontsource/inter`.

Not mounted / dead: `Header.jsx`, `RightSidebar.jsx`, `Main.jsx`, `Notification.jsx` (see Known Issues).

---

## 5. Data model

### PostgreSQL (`backend/schema.sql`)
| Table | Columns (notable) | Notes |
|---|---|---|
| `notebooks` | `notebook_id` PK, `notebook_name`, `created_at` | Top-level grouping unit |
| `files` | `file_id` PK, `notebook_id` FK→`notebooks` (CASCADE), `file_name`, `file_size`, `file_status`, `created_at` | `file_status` ∈ {`processing`, `ready`, `error`} (set in code) |
| `messages` | `message_id` PK, `notebook_id` FK→`notebooks` (CASCADE), `role` (CHECK ∈ `user/assistant/error`), `text`, `sources` jsonb, `created_at` | Chat history per notebook |
| `embeddings_test` | `embedding_id` PK, `file_id` FK→`files` (CASCADE), `chunk_text`, `embedding vector(1024)`, `text_search tsvector` (generated, English), `metadata` jsonb, `chunk_index` | Chunk store; naming retains an early `_test` suffix |

Indexes: HNSW on `embedding` (`vector_cosine_ops`); GIN on `text_search`.

---

## 6. Ingestion pipeline (per file)

Triggered by `POST /api/files/{file_id}/process` as a FastAPI background task → `services/file_processor.run_rag_pipeline`:

1. Load metadata; locate `backend/uploads/{notebook_id}/{file_id}.pdf` (note: always stored as `.pdf`).
2. Parse with **Docling** (`MarkdownDocument`), fallback to LangChain PDF loader (`pipeline.py document_loader`).
3. Split by Markdown headers (`#/##/###` → H1/H2/H3 section metadata) via `MarkdownHeaderTextSplitter`; semantic chunking exists but is commented out.
4. Embed each chunk with **BGE-M3** (1024-dim).
5. Store chunks in `embeddings_test`.
6. Mark file `ready`; on any failure mark `error`.

---

## 7. Retrieval & answer flow

Streaming chat (`POST /api/prompt/stream`, the path the UI uses):

1. **Retrieve context** — `VectorRAG.retrieve_context(notebook_id, user_prompt)`:
   - Vector cosine search (pgvector) and keyword full-text search (`websearch_to_tsquery`) in Postgres.
   - Merge candidates with Reciprocal Rank Fusion.
   - **Rerank** the top candidates with the BGE reranker, keep above threshold (top-3 fallback).
2. **Format** context into numbered source blocks; extract deduped `sources`.
3. **Rewrite** the user prompt using the last 6 messages (`services/rewritter.py`).
4. **Stream** the enriched prompt to `qwen2.5-coder-14b` at `LLM_URL`, relaying tokens to the client.

SSE event shape: `data: {json}\n\n` lines with `{"type":"sources", ...}`, `{"type":"response"| token under "response"}`, `{"type":"error", ...}`, then `data: [DONE]`.

Non-stream endpoint `POST /api/prompt` uses the same `VectorRAG` retrieval and returns `{"chatbot_response", "sources"}`.

---

## 8. HTTP API surface

| Method & path | Purpose | Notes |
|---|---|---|
| `GET /api/health` | Health check | `{"status":"ok"}` |
| `GET /api/notebooks` | List notebooks | ordered by `created_at DESC` |
| `POST /api/notebooks` | Create notebook | body `{notebook_name}`; optional `notebook_id` |
| `PUT /api/notebooks/{id}` | Rename | body `{notebook_name}` |
| `DELETE /api/notebooks/{id}` | Delete notebook | Postgres (files/messages/embeddings cascade) |
| `GET /api/notebooks/{id}/files` | List files | returns `{id,name,size,status}` |
| `POST /api/notebooks/{id}/files` | Register file row | status `processing` |
| `PATCH /api/files/{file_id}/status` | Update status | body `{status}` |
| `DELETE /api/files/{file_id}` | Delete file | Postgres (embeddings cascade) |
| `POST /api/files/upload` | Upload multipart | `notebook_id` + `file` (saved as `{file_id}.pdf`) |
| `POST /api/files/{file_id}/process` | Start background ingestion | returns `{"message":"processing started"}` |
| `GET /api/notebooks/{id}/messages` | List chat history | ordered by `created_at ASC` |
| `POST /api/notebooks/{id}/messages` | Save a message | body `{role, text, sources?}` |
| `POST /api/prompt/stream` | Streamed Q&A (SSE) | Uses `VectorRAG`; recommended path |
| `POST /api/prompt` | Non-streamed Q&A | Uses `VectorRAG` |

---

## 9. Known Issues & Deviations

Tracked items where the code differs from intent or is broken/unfinished. File references point at the current source. This list is the source of truth for `docs/PLAN.md` fix items.

| # | Issue | Location |
|---|---|---|
| 1 | **Env-var split:** `app.py` read `LLM_API_URL` while every service/rag call reads `config.LLM_URL` (env `LLM_URL`). **Fixed:** removed the unused read from `app.py`; all LLM calls use `LLM_URL`. | `backend/app.py` (fixed) |
| 2 | **Non-stream `/api/prompt`:** called `VectorRAG.retrieve_context(user_prompt)` missing the required `notebook_id`. **Fixed:** now passes `request.notebook_id`. | `backend/routes/llm.py` (fixed) |
| 3 | **Frontend port mismatch:** `/process` trigger hardcoded to `http://localhost:5000`. **Fixed:** now uses `API` from `src/config.js`. | `frontend/src/hooks/notebooks/useFiles.js` (fixed) |
| 4 | **Broken upload error path:** `uploadFileAPI` referenced undefined `uuidv4`/`setFiles`. **Fixed:** error branch now throws. | `frontend/src/services/files.js` (fixed) |
| 5 | **Wrong id in error branch:** `useMessages` persisted an error with bare `id` (undefined). **Fixed:** now uses `notebook_id`. | `frontend/src/hooks/notebooks/useMessages.js` (fixed) |
| 6 | **Dead code / unused:** `all-MiniLM-L6-v2` path, Ollama vars in `config.py`; `BaseRAG` unused; commented-out semantic chunker in `pipeline.py`. | `backend/config.py`, `backend/rag/base.py`, `backend/rag/pipeline.py` |
| 7 | **Hardcoded Windows model paths** (`D:\models\...`) and hardcoded `.pdf` storage suffix regardless of uploaded type → not portable; only PDFs ingest reliably. | `backend/config.py`, `backend/routes/files.py:178` |
| 8 | **Unmounted/dead UI:** `Header`, `RightSidebar`, `Main`, `Notification` are not rendered; several exported API functions and `sendMessage` (non-stream) are unused. | `frontend/src/pages/Notebook.jsx`, `frontend/src/components/notebook/` |
| 9 | **Test coverage is minimal & environment-bound:** the single test loads local models on app construction and only checks `/api/health`. | `backend/tests/test_app.py` |
| 10 | **Legacy naming:** the embeddings table is `embeddings_test`; file display uses `{id,name,size,status}` while DB columns are `file_*`. | `backend/schema.sql`, `backend/routes/files.py` |
