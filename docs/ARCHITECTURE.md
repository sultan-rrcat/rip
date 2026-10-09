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
│  │ BGE-M3 embed +    │    │ Agents: reasoning              │  │
│  │ hybrid search +   │    │ Tools: rag.query, notebook.inspect,│  │
│  │ BGE rerank        │    │   doc.generate, doc.convert,     │  │
│  └──────────────────┘    │   plot.chart                   │  │
│                           │ Provider: Ollama (direct)      │  │
│                           └────────────────────────────────┘  │
│                                                               │
│  API: /v1/runs · /v1/runs/{id}[/events|/cancel|/artifacts/*]  │
│       /v1/admin/health · /api/health (+ /health)              │
│       /api/notebooks · /api/notebooks/{id}/messages           │
│       /api/notebooks/{id}/files · /api/files/upload           │
│       /api/files/{id}/status · /api/files/{id}/process        │
│       /api/auth/login · /api/auth/logout · /api/auth/me       │
│  Auth: cookie session (`rip_session`); all /api/* + /v1/*     │
│  except /api/health, /health, /api/auth/* require login;      │
│  notebooks/files/messages/runs/artifacts scoped by owner      │
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
| `main.py` | Lifespan (`VectorRAG` singleton + `/v1` runtime composition), route mounts with auth gate (`auth.get_current_user` on all `/api/*` + `/v1/*` except health + `/api/auth/*`), CORS from `CORS_ORIGINS` (plain str; `*` = no credentials, explicit list = `credentials:include`) |
| `core/config.py` | `Settings` (port 8000, `OLLAMA_*`, BGE aliases, `cors_origins: str`) |
| `core/db.py`, `dependencies.py`, `logging.py` | `pg_connection()`, `get_rag()`, JSON logging |
| `core/classutils.py`, `constants.py` | Abstract checks, confidence constants |
| `rag/pipeline.py`, `vector_rag.py` | Ingest (Docling → header chunks → BGE-M3 embeddings → store); hybrid retrieve (vector + FTS, RRF, CrossEncoder rerank) |
| `routes/notebooks.py`, `files.py`, `messages.py` | `/api/*` notebook CRUD, uploads, message rows (all owner-scoped, 404-on-foreign) |
| `routes/auth.py` | `/api/auth/login|logout|me` cookie sessions (`rip_session`, 5/min + 15-min lockout), no auth dependency |
| `services/file_processor.py`, `chat.py` | Background ingest job; context formatting + source extraction |
| `providers/base.py`, `ollama.py`, `streaming.py` | `ModelProvider` contract, Ollama OpenAI-compat client, `<think>` filtering |
| `providers/tracing.py` | `wrap_provider()` — records `llm.generate[.stream|_structured]` generations (no-op when Langfuse off) |
| `agents/base.py`, `registry.py`, `provider_agent.py` | Agent contract + `ProviderAgent` shared base (common execute logic, watchdog) |
| `agents/reasoning.py`, `agents/coding.py` | Concrete agents — specify only system prompt, budget default, optional watchdog |
| `tools/base.py`, `registry.py`, `executor.py` | Tool contract + fixed 5-tool set, direct execution (no approval gate) |
| `tools/rag_query.py`, `notebook_inspect.py`, `plot_chart.py`, `doc_generate.py`, `doc_convert.py` | The five tools |
| `orchestration/plan.py`, `results.py` | Plan DAG models, step/execution results |
| `orchestration/router.py` | L1 intent router — sole dispatcher, one `generate_structured` call (`{intent, confidence}`; `<0.6` → `unknown` → ReAct; failures fail open to ReAct) |
| `orchestration/intents.py` | `Intent` enum + `ROUTER_CONFIDENCE_THRESHOLD=0.6` + `DETERMINISTIC_INTENTS` (10 builder intents) |
| `orchestration/builders.py` | L2 deterministic builders — code-built DAGs for `chat/qa_single/compare_multi/summarize/summarize_plot/plot_standalone/quiz/convert_one/convert_all/code` (`_PER_FILE_TOP_K=4`, `>5` files → ReAct) |
| `orchestration/react_engine.py` | L3 ReAct engine — thin wrapper around `ReactLoop`; pure helpers + `run_react` entry point |
| `orchestration/react_loop.py` | L3 ReAct loop — extracted from `ReActEngine.run()` for testability; all guard state on the class, each guard a method |
| `orchestration/planner.py` | Thin provider holder shared by the L1 router and L3 ReAct (no DAG prompt; mega-prompt removed per ADR-032) |
| `orchestration/validator.py` | Pure-rules gate: exactly-one executor, known ids, DAG-acyclic, step budget, plot/report grounding |
| `orchestration/engine.py` | Outer LangGraph: `plan → execute → aggregate` (+ `plan_error → END`) |
| `orchestration/plan_graph.py` | Inner per-request DAG: edges = `depends_on`, parallel siblings, placeholder resolution, scoped memory context (terminal prose agents only; tools get none), retry, timeout, cancel |
| `orchestration/orchestrator.py` | Façade: trace id, per-request config, `OrchestrationError` contract; delegates ReAct fallback to `ReactFallback` |
| `orchestration/react_fallback.py` | L3 ReAct fallback — extracted from `Orchestrator.run()` for testability; runs ReAct, aggregates, maps to `OrchestrationResult` |
| `orchestration/aggregator.py` | Deterministic type-aware answer assembly (no LLM): terminal text shown, intermediates hidden |
| `orchestration/memory.py` | Context-window summary + recent window |
| `store/runs.py` | Postgres CRUD for `runs` + gap-free `run_events` seq |
| `runs/manager.py` | Thread-per-run worker: memory load/persist, `sources`/`artifacts` events, terminal states |
| `artifacts.py` | Tool outputs → disk + download URLs |
| `bff/envelope.py` | SSE event vocabulary |
| `api/runs.py`, `admin.py`, `health.py`, `deps.py` | `/v1` run lifecycle, health stub, runtime DI |
| `observability/langfuse.py` | `init_langfuse` / `manual_span` / `manual_generation` / `request_attributes` / `truncate` / `flush` |

---

## 3. Data model (`backend/schema.sql`, 8 tables)

| Table | Key columns |
|---|---|
| `users` | `user_id`, `username` (unique), `password_hash` |
| `notebooks` | `notebook_id`, `notebook_name`, `owner_id` (FK → `users`, 404-on-foreign), `conversation_summary` (internal memory), `summary_message_count` |
| `files` | `file_id`, `notebook_id`, `file_name`, `file_size`, `file_status` (`uploading/processing/ready/error`) |
| `embeddings` | `file_id`, `chunk_text`, `embedding vector(1024)` + HNSW, `chunk_index` (overview stratification), `metadata`, `text_search tsvector` + GIN |
| `messages` | `notebook_id`, `role` (`user/assistant/error`), `text`, `sources`, `artifacts` (chart/file refs for inline preview) — **frontend-owned, backend never writes** |
| `runs` | `id`, `notebook_id`, `status` (`pending/running/completed/failed/cancelled`), `goal`, `plan`, `result` |
| `run_events` | `run_id`, `seq` (gap-free, structural only; `delta` live-only, never persisted), `event_type`, `payload` |
| `sessions` | `token` (cookie `rip_session`), `user_id`, `expires_at` |

---

## 4. Run lifecycle

1. Frontend ensures login (`GET /api/auth/me`, else `POST /api/auth/login` with `credentials:include`) then persists the user message: `POST /api/notebooks/{id}/messages`.
2. Frontend creates the run: `POST /v1/runs {notebook_id, message}` → `202 {run_id}` (bare JSON, no envelope).
3. Worker loads `conversation_summary` + messages + file snapshot → `build_memory_context()` → `orchestrator.run(..., context=..., notebook_context=...)`.
4. **Router** (L1, sole dispatcher, one cheap `generate_structured` call per request) classifies intent + slots; **Builders** (L2) emit fixed DAGs for `chat/qa_single/compare_multi/summarize/summarize_plot/plot_standalone/quiz/convert_one/convert_all/code` (wiring by construction; `plot_standalone` labels+values are parsed from the message, so they match by construction; `summarize_plot` funnels overview shards → one `numbers` step emitting strict `{"labels", "values"}` JSON → `plot.chart` `data`, so labels pair by construction — ADR-041); **Validator** checks plot/report grounding. Builder misses (unknown intent, non-deterministic shapes, unresolvable converts, `>5` files, unparseable standalone numbers) return `plan_error → END` and the orchestrator runs **ReAct** (L3: thought → action → observation, max 6 iterations, no placeholders) before failing honestly. Empty plans are repaired to a single `reasoning` step (ADR-026). No planner recall: partial/failed runs surface honestly; clarifications never replan (ADR-032).
5. **Engine** executes the DAG (`notebook_id` injected into tool inputs, never LLM-generated; memory `context` scoped to terminal prose agent steps, tools get none). `rag.query` completions emit SSE `sources`.
6. **Aggregator** assembles the answer deterministically (ADR-023, type-aware): single terminal output → verbatim (chart/SVG outputs → placeholder `Chart generated — see Artifacts below.`); multiple → labeled concat (same placeholder per chart step); intermediates (`chunks`/`numbers`/`notebook.inspect`) hidden unless sole output; clarification → verbatim; all-failed → joined errors; partial failures always appended.
7. Worker persists updated memory, writes the terminal run row, emits `artifacts` (download URLs; charts render inline as `<img>`) + `summary` + `run_completed`. Frontend persists the assistant message once, with sources + artifacts.
8. `GET /v1/runs/{id}/events` replays persisted events (`id:<seq>`, dedupe by `seq`); `delta` frames are live-only with fractional seqs and stream into the main bubble (Steps panel is mirror-only, ephemeral). `POST /v1/runs/{id}/cancel` cooperatively cancels.

SSE vocabulary (11 types, emit order): `run_started · plan · step_started · delta (live-only, fractional seq, never persisted) · step_completed · sources · artifacts · summary · run_completed · error · cancelled`.

---

## 5. Retrieval (RAG)

Ingest (`services/file_processor.py`, background task): PDF→Markdown via the `RAG_PDF_LOADER` loader (Docling default, OpenDataLoader alternative; the other is the fallback) → `MarkdownHeaderTextSplitter` (H1/H2/H3 + `{source,H1,H2,H3}` metadata) → BGE-M3 embeddings → `embeddings` table. Loader pages are joined **before** header splitting (ADR-034): a heading at a page boundary has no body under it and would otherwise be dropped, silently unlabelling that section's content. Storage replaces the file's rows in one transaction, so re-processing is idempotent.

Retrieve (`rag/vector_rag.py:retrieve_context(notebook_id, query, top_k=4, file_id=None, mode=specific|overview)`): pgvector cosine + full-text `ts_rank_cd` → dedupe → RRF (k=60) → BGE CrossEncoder rerank → threshold/filter, capped at `top_k`. `file_id`/`file_name` scope both SQL paths to one file; `mode=overview` fetches `top_k*3` candidates, boosts overview sections (H1/H2/H3 keyword match), and stratifies one chunk per H1 in `chunk_index` order. Deterministic builders fan out one file-scoped shard per ready file (compare → `specific`, summarize/quiz → `overview`) into a single reduce step. `rag.query` reuses the lifespan `VectorRAG` singleton (never re-instantiated — PyTorch weights are heavy).

Whole-file early return (ADR-033): before any retrieval work, `VectorRAG.retrieve_whole_file` probes `SUM(char_length(chunk_text))`/`COUNT(*)` over the same scope. If the scope fits `int(ollama_context_window * rag_whole_file_pct) * 4` chars (15% per shard, `len//4` estimator shared with `memory.py`) and ≤400 chunks, every chunk is returned in `chunk_index` order with `rerank_score=None`, and `rag.query` skips both the sub-query planner LLM call and the whole embed/vector/FTS/RRF/rerank pipeline. Otherwise it returns `None` and the ranked path runs unchanged. `OLLAMA_CONTEXT_WINDOW` must equal the Ollama server's `OLLAMA_CONTEXT_LENGTH` — `num_ctx` is ignored on the `/v1/chat/completions` endpoint the provider uses (see ADR-033).

---

## 6. Memory

`orchestration/memory.py`: token estimate `len//4`; when accumulated history exceeds ~70% of `ollama_context_window` (32768), Ollama folds aged-out turns into `notebooks.conversation_summary` (max 512 tokens). Recent window (`memory_window_size=10`) stays verbatim. `conversation_summary` is internal state — never rendered; distinct from the SSE `summary` event (the final answer).

The same `len//4` estimator and `ollama_context_window` bound retrieved context (ADR-033): one whole-file dump may claim `rag_whole_file_pct` (15%) of the window, because up to 5 file-scoped shards are concatenated into a single reduce prompt. `ollama_context_window` must match the Ollama server's `OLLAMA_CONTEXT_LENGTH`, because `num_ctx` is not enforced on the `/v1/chat/completions` endpoint `providers/ollama.py` uses.

---

## 7. Observability (opt-in Langfuse)

One trace per run worker (`session_id = notebook_id`, `trace_name = run`). Span tree:

```
run
├─ router → llm.generate_structured (L1 intent router; one call per request)
├─ plan (L2 builder hit only; absent when delegating to ReAct)
│  └─ step:{id} → llm.generate[.stream] (agent LLM)
├─ aggregate (deterministic, no LLM)
└─ react → react:iter-N → step:rN (L3 ReAct, max 6 iterations)
```

`router` is a trace-only sibling of `plan` under `run` (explicit `trace_context` parenting; the code still runs inside the plan node — no graph/state change). `plan` output carries `layer` (`L2-builder`) + `intent`/`routed_by`/`confidence`; `router` output carries `intent`/`confidence`/`routed_by`; each `react:iter-N` output carries `thought`/`executor`/`observation`. The SSE `plan` event carries an additive `route: {intent, routed_by, confidence}` object (old clients ignore it) and a constant `attempt: 1` (no recall; kept for old clients).

Enabled only with `LANGFUSE_ENABLED=true` + keys + backend restart; disabled path is behavior-identical.
