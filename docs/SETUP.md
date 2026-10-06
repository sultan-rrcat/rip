# Setup Guide: RIP

> Design reference: `docs/ARCHITECTURE.md`.

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

**Config contract:** `backend/app/core/config.py` is the single source of truth for all non-secret defaults. `.env` carries **only secrets and host-local deploy keys** (ports, DB connection, Ollama endpoint, model paths, Langfuse keys) — see `.env.example` for the minimal shape.

### `.env` keys (secrets + deploy)

| Key | Value |
|---|---|
| `HOST_BACKEND_PORT` / `HOST_FRONTEND_PORT` / `HOST_PG_PORT` | Host port mappings for compose (defaults: `8005` / `5173` / `5436`) |
| `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` | `postgres/5432/rip/rip/rippass` in compose; **host-local runs use `127.0.0.1` + `DB_PORT=<HOST_PG_PORT>`** (see CAVEATS — bare `localhost` can hang; sync `DB_PORT` to the mapped host port). Split-brain warning: the backend reads `.env` via `env_file`, but `POSTGRES_*` interpolate from the *shell* — stale shell `DB_*` exports shadow `.env` (see CAVEATS) |
| `OLLAMA_BASE_URL` | Interpolated: `${OLLAMA_BASE_URL:-http://host.docker.internal:11434}` — your `.env` value flows into the container, gateway is the fallback. Host-local default `http://localhost:11434` |
| `OLLAMA_DEFAULT_MODEL` | Override the code default (`ornith-1.5:9b`) if needed |
| `BGE_M3_MODEL_PATH` / `BGE_RERANKER_V2_M3` | Absolute host-local paths to embedding models (compose overrides to `/app/backend/models/...`) |
| `LANGFUSE_SECRET_KEY` / `LANGFUSE_PUBLIC_KEY` | Opt-in tracing keys (get from Langfuse UI — org settings → API keys; see §4) |

### Code defaults (`config.py` — not in `.env`)

| Key | Default |
|---|---|
| `PORT` | `8005` (compose hard-pins `PORT=8000` in-container; see `docker-compose.yml`) |
| `OLLAMA_TIMEOUT_MS` | `300000` (Ollama HTTP timeout) |
| `OLLAMA_CONTEXT_WINDOW` | `32768` (memory budget = `*SUMMARY_THRESHOLD_PCT` ≈ 22900 tokens, `len//4` estimator). **Must equal the Ollama server's `OLLAMA_CONTEXT_LENGTH`** — the backend posts to `/v1/chat/completions`, where `num_ctx` is silently ignored (verified 2026-10-01; only native `/api/chat` enforces it), so this value does not size the server window. Mismatching it silently mis-sizes both the memory fold and the whole-file RAG gate (ADR-033) |
| `DEFAULT_TEMPERATURE` | `0.1` |
| `DEFAULT_MAX_TOKENS` | `2048` (default per-step output cap; QA/summarize/compare/quiz ride this) |
| `CODING_MAX_TOKENS` | `32768` (coding-agent output cap — test scripts + file echoes are long; set per step by the CODE builder, overridable via step input `max_tokens`) |
| `CHAT_MAX_TOKENS` | `1024` (chat builder cap — greetings/small-talk stay cheap) |
| `MAX_UPLOAD_SIZE_MB` | `50` (must match nginx `client_max_body_size 50M`) |
| `RAG_WHOLE_FILE_PCT` | `0.15` — share of the window one whole-file `rag.query` dump may fill before falling back to ranked retrieval (ADR-033). Per-shard, since up to 5 shards share one reduce prompt; `0` disables the shortcut |
| `RAG_PDF_LOADER` | `docling` (default) or `opendataloader` — the selected PDF→Markdown loader runs first, the other is the fallback |
| `CORS_ORIGINS` | `*` (plain str, not `["*"]`) |
| `UPLOAD_DIR` | `./backend/uploads` host-local; `/app/uploads` in compose (overridden in `docker-compose.yml`, image `ENV` fallback matches) |
| `LANGFUSE_ENABLED` | `true` (opt-in tracing; keys from `.env`) |
| `BACKEND_CPUS` | Backend CPU quota (compose `cpus:` time quota, not core pinning). Dynamic default via `scripts/rip.*`: host cores − 1 (min 2); explicit shell/`.env` wins; raw compose falls back to `2.0`. Raise on big hosts for faster Docling/BGE ingestion |

No `LLM_URL`, no `NEO4J_*`, no `DATABASE_URL`.

## 3. Database bootstrap

```powershell
psql "host=<DB_HOST> port=<HOST_PG_PORT> dbname=<DB_NAME> user=<DB_USER>" -f backend\schema.sql
```

Creates `users`, `notebooks` (with `notebook_name` + `owner_id` + `conversation_summary` + `summary_message_count`), `files`, `messages`, `embeddings` (with `chunk_index`), `runs`, `run_events`, `sessions`, vector + text-search indexes.
Use `HOST_PG_PORT` (default 5432, live override e.g. 5433) — not hardcoded
`5432`. Host-side ports are all adjustable the same way: `HOST_PG_PORT`,
`HOST_BACKEND_PORT` (default 8000), `HOST_FRONTEND_PORT` (default 5173).
Container ports stay fixed (`5432`, `8000`, `8080` — frontend nginx runs as non-root `USER nginx`). `schema.sql` also mounts as Postgres init (`001-schema.sql`) but runs
only on an empty `pgdata` volume; re-apply via `psql` after DDL changes.

## 4. Run (Required: Postgres + Ollama. Optional: Langfuse)

Use the lifecycle scripts (`scripts/rip.ps1` on Windows, `scripts/rip.sh`
on Linux/macOS — same commands). On first run the script copies
`.env.example → .env` for you; edit it for your run mode (host-local
Ollama stays `OLLAMA_BASE_URL=http://localhost:11434`; for compose point
it at the host gateway or a LAN remote — the `.env` value flows into the
container, gateway is only the fallback when unset), then run again:

```powershell
scripts/rip.ps1 up
# scripts/rip.sh up   (Linux/macOS)
```

| Command | Effect |
|---|---|
| `up [services]` | `up -d --build` + wait healthy + URLs (`--build` is the default: `VITE_API_URL` is baked at image build, so plain `up` reuses the old bake) |
| `down` | Stop/remove containers (volumes kept) |
| `fresh [-y]` | **Wipe `pgdata` + `uploads`** (`down -v`), then `up --build` (asks unless `-y`) |
| `restart [service]` | Bounce without rebuild |
| `rebuild [service]` | `up -d --build` scoped (default: all) |
| `logs [service] [--tail N]` | Follow logs |
| `ps` / `status` | Service table |
| `migrate` | Re-apply `backend/schema.sql` to the running postgres (no host `psql` needed) |
| `health` | Probe backend `/api/health`, frontend `/healthz`, postgres; print URLs |
| `help` | Usage |

The scripts clear stale shell `DB_*` exports before every compose call
(they shadow `.env` for `POSTGRES_*` interpolation — see CAVEATS), probe
`127.0.0.1` (never bare `localhost`), and read host ports from
`HOST_*_PORT` in `.env`. Langfuse runs as a separate stack (see CAVEATS);
backend reaches it via host gateway
(`extra_hosts: host.docker.internal:host-gateway`, portable Windows/Linux).
Compose pins container `PORT=8000` (do not override; `.env` `PORT` only
affects host-local runs); health gating chains
postgres → backend → frontend (frontend starts only when backend is healthy).

Local alternative:

```powershell
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
cd ..\frontend
npm install; npm run dev
```

Health: `GET http://localhost:8000/api/health` → `{"status":"ok"}` (alias `GET /health`). Substitute `HOST_BACKEND_PORT` when remapped (e.g. `http://localhost:8005/api/health`).
Vite dev (`npm run dev`, port `5178` per `vite.config.ts`) proxies `/api/` + `/v1/` → `http://localhost:8000`; compose-host frontend is `HOST_FRONTEND_PORT` (default `5173`) → container `8080`. Prod `nginx.conf` must proxy both. Empty `VITE_API_URL` = same-origin; nginx allows 50M uploads (`client_max_body_size 50M`, matching `MAX_UPLOAD_SIZE_MB=50`) with unbuffered SSE (`/api/` 60s, `/v1/` 600s timeouts).

## 5. Smoke test

0. `scripts/rip.ps1 health` — backend, frontend, postgres all OK.
1. Login (`POST /api/auth/login`, cookies kept) — all `/api/*` + `/v1/*` except health require it.
2. Create notebook, upload PDF → `ready`.
3. `POST /v1/runs {notebook_id, message}` → `202 {run_id}`.
4. `GET /v1/runs/{id}/events` streams `run_started→plan→step_started→delta* (live-only)→step_completed→sources?→artifacts?→summary→run_completed` (`error`/`cancelled` on failure paths).
4. Confirm `rag.query` chunks + Ollama answer + `sources` SSE event; notebook `conversation_summary` updates when context window ~70% full.

## 6. Tests & lint

| Tool | Command | Location |
|---|---|---|
| Backend tests | `pytest` | root (`testpaths = ["backend/tests"]`) |
| Backend lint | `ruff` | root |
| Frontend lint | `npm run lint` | `frontend/` |

## Troubleshooting

- **DB connect fail** → check `DB_*`, pgvector extension, `scripts/rip.ps1 migrate` to re-apply `schema.sql` (compose init runs once — see CAVEATS).
- **Ollama empty** → `OLLAMA_BASE_URL` reachable (host-local: `http://localhost:11434`; in-compose: your `.env` value — host gateway or LAN remote, gateway is the fallback), planner model pulled.
- **Startup crash (models)** → BGE paths wrong; fix env.
- **Upload stuck `processing`** → background `run_rag_pipeline` has no retry; re-`POST /process`.
