# Setup Guide: RIP

> Design reference: `docs/ARCHITECTURE.md`. Gotchas: `docs/CAVEATS.md` (read before debugging).

---

## 1. Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.11+ (per `pyproject.toml`) |
| Node.js | 20.19+ or 22.12+ |
| PostgreSQL | pgvector/pgvector:pg16, extension `vector` enabled |
| Ollama | Reachable at `OLLAMA_BASE_URL`, planner model pulled (default `qwen2.5:14b`; local override possible, see CAVEATS) |
| Local weights | BGE-M3 + reranker on disk (paths below) |

Model paths (Pydantic `backend/app/core/config.py`):

| Key | Default |
|---|---|
| `BGE_M3_MODEL_PATH` | `/abs/path/to/backend/models/bge-m3` (relative auto-resolves to repo-root absolute) |
| `BGE_RERANKER_V2_M3` | `/abs/path/to/backend/models/reranker/bge_reranker_v2_m3` |

## 2. Environment

```powershell
copy .env.example .env
```

Keys ( authoritative defaults in `backend/app/core/config.py`):

| Key | Value |
|---|---|
| `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` | `postgres/5432/rip/rip/rippass` in compose; **host-local runs use `127.0.0.1` + `HOST_PG_PORT`** (see CAVEATS — bare `localhost` can hang) |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` in compose, `http://localhost:11434` local |
| `OLLAMA_DEFAULT_MODEL` | `qwen2.5:14b` (config default; a local `.env` may override, e.g. a smaller planner model) |
| `OLLAMA_CONTEXT_WINDOW` | `32768` (budget = `*0.7` ≈ 22900 tokens, `len//4` estimator) |
| `PORT` | `8000` |
| `CORS_ORIGINS` | `*` (plain str, not `["*"]`) |
| `UPLOAD_DIR` | `./backend/uploads` |

No `LLM_URL`, no `NEO4J_*`, no `DATABASE_URL`.

## 3. Database bootstrap

```powershell
psql "host=<DB_HOST> port=5432 dbname=<DB_NAME> user=<DB_USER>" -f backend\schema.sql
```

Creates `notebooks` (with `conversation_summary` + `summary_message_count`), `files`, `messages`, `embeddings`, `runs`, `run_events`, vector + text-search indexes.

## 4. Run (Required: Postgres + Ollama. Optional: Langfuse)

```powershell
docker compose up postgres backend frontend
# Langfuse runs as a separate stack (see CAVEATS); backend reaches it via host gateway
```

Local alternative:

```powershell
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
cd ..\frontend
npm install; npm run dev
```

Health: `GET http://localhost:8000/api/health` → `{"status":"ok"}` (alias `GET /health`).
Vite dev proxies `/api/` + `/v1/` → `http://localhost:8000` (see `vite.config.ts`); prod `nginx.conf` must proxy both.

## 5. Smoke test

1. Create notebook, upload PDF → `ready`.
2. `POST /v1/runs {notebook_id, message}` → `202 {run_id}`.
3. `GET /v1/runs/{id}/events` streams `run_started→plan→step_started→delta* (live)→step_completed→sources?→summary→run_completed`.
4. Confirm `rag.query` chunks + Ollama answer + `sources` SSE event; notebook `conversation_summary` updates when context window ~70% full.

## 6. Tests & lint

| Tool | Command | Location |
|---|---|---|
| Backend tests | `pytest` | root (`testpaths = ["backend/tests"]`) |
| Backend lint | `ruff` | root |
| Frontend lint | `npm run lint` | `frontend/` |

## Troubleshooting

- **DB connect fail** → check `DB_*`, pgvector extension, `schema.sql` applied.
- **Ollama empty** → `OLLAMA_BASE_URL` reachable (host-local: `http://localhost:11434`; in-compose: `http://host.docker.internal:11434`), planner model pulled.
- **Startup crash (models)** → BGE paths wrong; fix env.
- **Upload stuck `processing`** → background `run_rag_pipeline` has no retry; re-`POST /process`.
