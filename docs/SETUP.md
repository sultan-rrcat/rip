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
| `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` | `postgres/5432/rip/rip/rippass` in compose; **host-local runs use `127.0.0.1` + `DB_PORT=<HOST_PG_PORT>`** (see CAVEATS — bare `localhost` can hang; sync `DB_PORT` to the mapped host port). Split-brain warning: the backend reads `.env` via `env_file`, but `POSTGRES_*` interpolate from the *shell* — stale shell `DB_*` exports shadow `.env` (see CAVEATS) |
| `OLLAMA_BASE_URL` | Interpolated: `${OLLAMA_BASE_URL:-http://host.docker.internal:11434}` — your `.env` value flows into the container, gateway is the fallback. Host-local default `http://localhost:11434` |
| `OLLAMA_DEFAULT_MODEL` | `qwen2.5:14b` (config default; a local `.env` may override, e.g. a smaller planner model) |
| `OLLAMA_CONTEXT_WINDOW` | `32768` (budget = `*0.7` ≈ 22900 tokens, `len//4` estimator) |
| `PORT` | `8000`, pinned in compose (`docker-compose.yml` sets `PORT: 8000` — do not override; `EXPOSE`, port mapping, and health probes all assume it). Honored as `$PORT` only for bare `docker run` |
| `CORS_ORIGINS` | `*` (plain str, not `["*"]`) |
| `UPLOAD_DIR` | `./backend/uploads` host-local; `/app/uploads` in compose (overridden in `docker-compose.yml`, image `ENV` fallback matches) |

No `LLM_URL`, no `NEO4J_*`, no `DATABASE_URL`.

## 3. Database bootstrap

```powershell
psql "host=<DB_HOST> port=<HOST_PG_PORT> dbname=<DB_NAME> user=<DB_USER>" -f backend\schema.sql
```

Creates `notebooks` (with `conversation_summary` + `summary_message_count`), `files`, `messages`, `embeddings`, `runs`, `run_events`, vector + text-search indexes.
Use `HOST_PG_PORT` (default 5432, live override e.g. 5433) — not hardcoded
`5432`. Host-side ports are all adjustable the same way: `HOST_PG_PORT`,
`HOST_BACKEND_PORT` (default 8000), `HOST_FRONTEND_PORT` (default 5173).
Container ports stay fixed (`5432`, `8000`, `80`). `schema.sql` also mounts as Postgres init (`001-schema.sql`) but runs
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
Vite dev proxies `/api/` + `/v1/` → `http://localhost:8000` (see `vite.config.ts`); prod `nginx.conf` must proxy both. Empty `VITE_API_URL` = same-origin; nginx allows 100M uploads with unbuffered SSE.

## 5. Smoke test

0. `scripts/rip.ps1 health` — backend, frontend, postgres all OK.
1. Create notebook, upload PDF → `ready`.
2. `POST /v1/runs {notebook_id, message}` → `202 {run_id}`.
3. `GET /v1/runs/{id}/events` streams `run_started→plan→step_started→delta* (live)→step_completed→sources?→artifacts?→summary→run_completed`.
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
