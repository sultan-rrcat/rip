# RIP Architecture — Research Intelligence Platform

Offline, single-codebase research assistant: document RAG + multi-agent orchestration + tool execution. One notebook = one conversation.

---

## 1. Topology

```
┌──────────────────────────────────────────────────────────────┐
│                  Frontend (React 19 + Vite)                   │
│  Notebook Gallery ← Notebook Workspace (Chat + Knowledge)     │
│  Streaming: POST /v1/runs + GET /v1/runs/{id}/events (SSE)   │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                   Backend (FastAPI, backend/app/)             │
│                                                               │
│  ┌──────────────────┐    ┌────────────────────────────────┐  │
│  │ RAG Pipeline      │    │ Orchestration (LangGraph)      │  │
│  │ Docling → chunk   │◄───│ Plan → Execute DAG → Aggregate │  │
│  │ BGE-M3 embed +    │    │ Agents: reasoning, coding,     │  │
│  │ hybrid search +   │    │   vision                       │  │
│  │ BGE rerank        │    │ Tools: rag.query, notebook.inspect,│  │
│  └──────────────────┘    │   doc.generate, doc.convert,     │  │
│                           │   plot.chart, code.sandbox,      │  │
│                           │   image.generate                 │  │
│                           │ Provider: Ollama (direct)      │  │
│                           └────────────────────────────────┘  │
│                                                               │
│  API: /v1/runs · /v1/runs/{id}[/events|/cancel|/artifacts/*]  │
│       /v1/admin/health · /api/notebooks · /api/files ·        │
│       /api/notebooks/{id}/messages · /api/health (+ /health)  │
└────────────────────────────┬─────────────────────────────────┘
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
  PostgreSQL + pgvector   Ollama            Langfuse (opt-in)
  (runs survive refresh)  (sole LLM)        (per-run traces)
```

Redis is optional (queue/cache only). Runs are Postgres-backed, so Redis is never required.

---

## 2. Backend layout (`backend/app/`)

| Path | Role |
|---|---|
| `main.py` | Lifespan (`VectorRAG` singleton + `/v1` runtime composition), route mounts, CORS `*` |
| `core/config.py` | `Settings` (port 8000, `OLLAMA_*`, BGE aliases, `cors_origins: str`) |
| `core/db.py`, `dependencies.py`, `logging.py` | `pg_connection()`, `get_rag()`, JSON logging |
| `core/classutils.py`, `constants.py` | Abstract checks, confidence constants |
| `rag/pipeline.py`, `vector_rag.py` | Ingest (Docling → header chunks → BGE-M3 embeddings → store); hybrid retrieve (vector + FTS, RRF, CrossEncoder rerank) |
| `routes/notebooks.py`, `files.py`, `messages.py` | `/api/*` notebook CRUD, uploads, message rows |
| `services/file_processor.py`, `chat.py` | Background ingest job; context formatting + source extraction |
| `providers/base.py`, `ollama.py`, `streaming.py` | `ModelProvider` contract, Ollama OpenAI-compat client, `<think>` filtering |
| `providers/tracing.py` | `wrap_provider()` — records `llm.generate[.stream|_structured]` generations (no-op when Langfuse off) |
| `agents/base.py`, `registry.py`, `reasoning.py`, `coding.py`, `vision.py` | Agent contract + fixed 3-agent set |
| `tools/base.py`, `registry.py`, `executor.py` | Tool contract + fixed 7-tool set, direct execution (no approval gate) |
| `tools/rag_query.py`, `notebook_inspect.py`, `plot_chart.py`, `doc_generate.py`, `doc_convert.py`, `code_sandbox.py`, `image_generate.py` | The seven tools |
| `orchestration/plan.py`, `results.py` | Plan DAG models, step/execution results |
| `orchestration/planner.py` | Sole planning LLM call (`generate_structured` vs `PLAN_SCHEMA`) |
| `orchestration/validator.py` | Pure-rules gate: exactly-one executor, known ids, DAG-acyclic, step budget |
| `orchestration/engine.py` | Outer LangGraph: `plan → execute → aggregate` (+ `plan_error → END`) |
| `orchestration/plan_graph.py` | Inner per-request DAG: edges = `depends_on`, parallel siblings, placeholder resolution, retry, timeout, cancel |
| `orchestration/orchestrator.py` | Façade: trace id, per-request config, `OrchestrationError` contract |
| `orchestration/aggregator.py` | Deterministic answer assembly (no LLM) |
| `orchestration/memory.py` | Context-window summary + recent window |
| `store/runs.py` | Postgres CRUD for `runs` + gap-free `run_events` seq |
| `runs/manager.py` | Thread-per-run worker: memory load/persist, `sources`/`artifacts` events, terminal states |
| `artifacts.py` | Tool outputs → disk + download URLs |
| `bff/envelope.py` | SSE event vocabulary |
| `api/runs.py`, `admin.py`, `health.py`, `deps.py` | `/v1` run lifecycle, health stub, runtime DI |
| `observability/langfuse.py` | `init_langfuse` / `manual_span` / `manual_generation` / `request_attributes` / `truncate` / `flush` |

---

## 3. Data model (`backend/schema.sql`, 6 tables)

| Table | Key columns |
|---|---|
| `notebooks` | `notebook_id`, `name`, `conversation_summary` (internal memory), `summary_message_count` |
| `files` | `file_id`, `notebook_id`, `file_status` (`uploading/processing/ready/error`) |
| `embeddings` | `file_id`, `chunk_text`, `embedding vector(1024)` + HNSW, `metadata`, `text_search tsvector` + GIN |
| `messages` | `notebook_id`, `role` (`user/assistant/error`), `text`, `sources`, `artifacts` (chart/file refs for inline preview) — **frontend-owned, backend never writes** |
| `runs` | `id`, `notebook_id`, `status` (`pending/running/completed/failed/cancelled`), `goal`, `plan`, `result` |
| `run_events` | `run_id`, `seq` (gap-free, structural only), `event_type`, `payload` |

---

## 4. Run lifecycle

1. Frontend persists the user message: `POST /api/notebooks/{id}/messages`.
2. Frontend creates the run: `POST /v1/runs {notebook_id, message}` → `202 {run_id}` (bare JSON, no envelope).
3. Worker loads `conversation_summary` + messages + file snapshot → `build_memory_context()` → `orchestrator.run(..., context=..., notebook_context=...)`.
4. **Planner** emits `{goal, steps}`; **Validator** checks it. Empty plans are repaired to a single `reasoning` step (ADR-026); malformed plans fail honestly via `plan_error → END`.
5. **Engine** executes the DAG (`notebook_id` injected into tool inputs, never LLM-generated). `rag.query` completions emit SSE `sources`.
6. **Aggregator** assembles the answer deterministically: 1 success → verbatim (chart/SVG outputs → placeholder `Chart generated — see Artifacts below.`); N successes → labeled concat (same placeholder per chart step); clarification → verbatim; all-failed → joined errors.
7. Worker persists updated memory, writes the terminal run row, emits `artifacts` (download URLs; charts render inline as `<img>`) + `summary` + `run_completed`. Frontend persists the assistant message once, with sources + artifacts.
8. `GET /v1/runs/{id}/events` replays persisted events (`id:<seq>`, dedupe by `seq`); `delta` frames are live-only with fractional seqs. `POST /v1/runs/{id}/cancel` cooperatively cancels.

SSE vocabulary: `run_started · plan · step_started · delta · step_completed · sources · summary · artifacts · run_completed · error · cancelled`.

---

## 5. Retrieval (RAG)

Ingest (`services/file_processor.py`, background task): Docling PDF→Markdown (fallback loader) → `MarkdownHeaderTextSplitter` (H1/H2/H3 + `{source,H1,H2,H3}` metadata) → BGE-M3 embeddings → `embeddings` table.

Retrieve (`rag/vector_rag.py:retrieve_context(notebook_id, query, top_k=8)`): pgvector cosine + full-text `ts_rank_cd` → dedupe → RRF (k=60) → BGE CrossEncoder rerank → threshold/filter. `rag.query` reuses the lifespan `VectorRAG` singleton (never re-instantiated — PyTorch weights are heavy).

---

## 6. Memory

`orchestration/memory.py`: token estimate `len//4`; when accumulated history exceeds ~70% of `ollama_context_window` (32768), Ollama folds aged-out turns into `notebooks.conversation_summary` (max 512 tokens). Recent window (`memory_window_size=10`) stays verbatim. `conversation_summary` is internal state — never rendered; distinct from the SSE `summary` event (the final answer).

---

## 7. Observability (opt-in Langfuse)

One trace per run worker (`session_id = notebook_id`, `trace_name = run`). Span tree:

```
run
├─ plan → llm.generate_structured (planner)
├─ step:{id} → llm.generate[.stream] (agent LLM)
└─ aggregate (deterministic, no LLM)
```

Enabled only with `LANGFUSE_ENABLED=true` + keys + backend restart; disabled path is behavior-identical. See `docs/CAVEATS.md` for host/proxy/API notes.
