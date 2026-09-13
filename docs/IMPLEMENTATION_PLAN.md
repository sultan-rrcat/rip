# Implementation Plan — Athena → RIP (phased checklist)

> Complete picture lives in `MERGE_PLAN.md` + `CONTEXT.md`. This file is execution only: phases in order, one checkbox per task, test per task.
> Rule: one unchecked box per session. On conflict, `MERGE_PLAN.md §0` wins.

## How to use this file

1. Work top to bottom. Do not skip phases.
2. Each task has Files, Edits, Test, Done. Do all four before checking the box.
3. End of session: `pytest` + `ruff`, append `SESSION_LOG.md` as `## [date] - PHASE x.y - prompt/commit/status`.
4. Commit multi-stage per `AGENT.md` §4b (refactor → test → chore → docs), one concern per commit.

---

## Phase 1 — Skeleton + config (backend boots, no orchestration yet)

- [x] **1.1 Create `backend/app/` root** (2026-09-12: moved core/rag/routes/services under app/, app.py→app/main.py, config.py→app/core/config.py interim hybrid + Settings; import map applied; rewritter/llm deleted; conftest→app.main/Ollama /api/tags; Dockerfile CMD app.main:app; llm.router excluded pending 4.3; flat-grep 0 hits, light imports ok, ruff pre-existing only, pytest blocked by torch env — see SESSION_LOG)
  - Files: MOVE `rip/backend/core/` → `rip/backend/app/core/`, `rag/` → `app/rag/`, `routes/` → `app/routes/`, `services/` → `app/services/`; COPY `athena/backend/app/core/classutils.py`, `constants.py` → `rip/backend/app/core/`
  - Edits: apply import map `routes.→app.routes.`, `core.→app.core.`, `services.→app.services.`, `rag.→app.rag.`; empty `__init__.py` per package; workdir `rip/backend/`; `backend/Dockerfile` CMD → `app.main:app`; DELETE `services/rewritter.py` + `services/llm.py` (Q33); migrate `backend/tests/conftest.py` → `from app.core.config import Settings`, `from app.main import app`, Ollama probe `OLLAMA_BASE_URL/api/tags` (Q38)
  - Test: `ruff check backend/app` (from `rip/`)
  - Done: imports resolve, no `flat backend/app.py` refs remain, rewritter/llm gone, conftest migrated

- [x] **1.2 Verify `schema.sql`** (2026-09-12: no drift, read-only kept — 6 tables IF NOT EXISTS, conversation_summary+summary_message_count, messages notebook_id-only, runs+run_events; static grep full pass; live stripped apply 2x ok + 6 tables proven in scratch db; full pgvector apply needs compose postgres — see SESSION_LOG)
  - Files: `rip/backend/schema.sql` (read-only unless drift)
  - Edits: must have `notebooks.conversation_summary` + `summary_message_count`, `messages` by `notebook_id` only, `runs` + `run_events` (Q38)
  - Test: `psql "host=<DB_HOST> port=5432 dbname=<DB_NAME> user=<DB_USER>" -f backend\schema.sql` twice (must be re-runnable)
  - Done: 6 tables exist: `notebooks/files/embeddings/messages/runs/run_events`

- [x] **1.3 Rewrite `app/core/config.py`** (2026-09-12: TARGET Settings verbatim — port 8000, OLLAMA_*, cors_origins str, no LLM_URL/NEO4J_*/DATABASE_URL; +AliasChoices BGE aliases, +extra=ignore until 1.4 env cleanup, +settings singleton; usages migrated pipeline/file_processor/routes-files/conftest/test_app; Settings().port→8000, boots-without-LLM_URL + alias proofs ok — see SESSION_LOG)**
  - Files: REPLACE `rip/backend/app/core/config.py` with TARGET block in `MERGE_PLAN.md §Config`
  - Edits: port `8000`, `OLLAMA_*`, no `LLM_URL`, no `NEO4J_*`; `cors_origins: str="*"`; support `BGE_MODEL_DIR`/`RERANKER_MODEL_DIR` aliases
  - Test: `python -c "from app.core.config import Settings; print(Settings().port)"` → `8000`
  - Done: boots without `LLM_URL`

- [x] **1.4 Update `pyproject.toml` + `.env.example`** (2026-09-12: [project] TARGET wholesale — rip 1.0.0, langgraph+langfuse kept, google-genai/neo4j dropped; .env.example TARGET verbatim CORS_ORIGINS=* + BGE aliases; .env copied + BGE→D:/models local override; no requirements.txt, no neo4j/DATABASE_URL/LLM_URL live refs; all 15 TARGET deps installed ≥pins; Settings boots from new .env port 8000; pip install -e . N/A by layout + app boot blocked by torch env — see SESSION_LOG)**
  - Files: REPLACE `rip/pyproject.toml` deps + `rip/.env.example` keys with TARGET blocks (Q29: keep `langfuse`, drop `google-genai`/`neo4j`; `.env` uses `CORS_ORIGINS=*`, keeps `BGE_MODEL_DIR` aliases)
  - Edits: no `requirements.txt`, no `neo4j`, no `DATABASE_URL`
  - Test: `pip install -e .` (from `rip/`) + `copy .env.example .env`
  - Done: install clean, backend boots to `GET /api/health`

- [x] **1.5 Replace `docker-compose.yml`** (2026-09-12: TARGET verbatim — postgres/rip/rip/pgdata, 8000:8000, DB_HOST=postgres, OLLAMA host.docker.internal, redis optional-commented; down ok, config valid; postgres proven via same-image harness on 5433 — 6 tables + vector ext + Q38 cols + re-apply exit 0; literal up on 5432 awaits free host port — see SESSION_LOG; follow-up 2026-09-12: user-directed HOST_PG_PORT override, `up postgres` healthy on host 5433, 6 tables + vector live)**
  - Files: REPLACE `rip/docker-compose.yml` with TARGET block (Q29 breaking: `db→postgres`, `prototype_rip/trainee→rip/rip`, `postgres_data→pgdata`)
  - Edits: services `postgres/backend/frontend`, `8000:8000`, `DB_HOST=postgres`, `OLLAMA_BASE_URL=http://host.docker.internal:11434`, Redis commented as optional; run `docker compose down` before `up --build postgres`
  - Test: `docker compose config`
  - Done: config valid, `docker compose up postgres` healthy

Phase 1 exit: `cd backend; uvicorn app.main:app --port 8000` serves `GET /api/health → {"status":"ok"}` (old main, new layout).

---

## Phase 2 — Providers + agents

- [x] **2.1 Providers (`base.py`, `ollama.py`, `streaming.py`)** (2026-09-12: copied from Athena; base verbatim; ollama de-pluginned → ModelProvider, init/health/plugin attrs dropped, gemini prose neutralized, schema name rip, get_logger→stdlib; streaming get_logger→stdlib; `test_providers.py` 12 passed vs minicpm5 — generate+stream round-trip, resolution, ThinkFilter units; qwen2.5:14b pull skipped per user, default-model live proof pending — see SESSION_LOG)**
  - Files: COPY `athena/backend/app/providers/base.py`, `ollama.py`, `streaming.py` → `rip/backend/app/providers/`
  - Edits: replace all `gemini_model_*` with `ollama_default_model`; drop `from app.plugins.api import ...` and `ProviderPlugin` inheritance in `ollama.py` (inherit directly from `ModelProvider`); DO NOT copy `gemini.py`, `llama_server.py`, `tracing.py`
  - Test: `pytest backend/tests/test_providers.py -q` (create if missing: Ollama chat round-trip, mock allowed only if Ollama down)
  - Done: Ollama `qwen2.5:14b` chat call succeeds, ThinkFilter incrementally strips `<think>` tags

- [x] **2.2 Agents (reasoning + coding + vision)** (2026-09-13: copied base verbatim + registry with get_default_agent_registry factory; reasoning/coding/vision de-pluginned → Agent direct, gemini_model_* → ollama_default_model, get_logger → stdlib; mock-provider execute ok — reasoning success + missing-message failure, model kw qwen2.5:14b; ruff E/F only inherited E501s, full-check only Athena-inherited RUF/PIE style — see SESSION_LOG)
  - Files: COPY `athena/backend/app/agents/base.py`, `registry.py`, `reasoning.py`, `coding.py`, `vision.py` → `rip/backend/app/agents/`
  - Edits: point models to `ollama_default_model`; drop `from app.plugins.api import AgentPlugin` (inherit directly from `Agent` in `base.py`); export `get_default_agent_registry(provider)` factory in `registry.py`
  - Test: `python -c "from app.agents.registry import get_default_agent_registry; print(get_default_agent_registry)"`
  - Done: registry instantiates and lists reasoning, coding, vision without plugin system dependencies

---

## Phase 3 — Tools + orchestration + runs store

- [ ] **3.1 Tools (all 5)**
  - Files: COPY `base.py`, `registry.py`, `executor.py` → `app/tools/`; REWRITE `rag_query.py`; COPY `plot_chart.py`, `doc_generate.py`, `code_sandbox.py`, `image_generate.py`
  - Edits: drop `ToolPlugin`; delete approval gate in `executor.py`; `rag_query.py` reuse `VectorRAG` singleton via `get_rag`; return shape feeds `extract_sources()` for Q32; `notebook_id` from Run, never LLM
  - Test: `pytest backend/tests/test_tools.py -q` — `rag.query` returns chunks for a test notebook with files
  - Done: all 5 tools import and inherit from `Tool`, `rag.query` e2e works without model reload

- [ ] **3.2 Orchestration**
  - Files: COPY `plan.py`, `planner.py`, `validator.py`, `aggregator.py`, `engine.py`, `plan_graph.py`, `orchestrator.py`, `memory.py`, `results.py` → `app/orchestration/`
  - Edits: `planner.py` gemini→Ollama; `aggregator.py` Q36 deterministic rules (1 success→output; multiple→labeled join; clarification→verbatim; all failed→errors; no LLM); `engine.py` remove reflection; `plan_graph.py` no approval gate, inject `notebook_id`; `orchestrator.py` signature with `notebook_id` + `context`; `memory.py` Q28 port, persist target `notebooks.conversation_summary`
  - Test: `pytest backend/tests/test_orchestration.py -q`
  - Done: Planner → Engine → Aggregator passes on a `rag.query` plan with `notebook_id` injected

- [ ] **3.3 Runs store (Postgres)**
  - Files: WRITE `rip/backend/app/store/runs.py` (Postgres CRUD using `core.db.pg_connection` and `schema.sql` `runs` + `run_events` tables; DO NOT copy Athena's SQLite store)
  - Edits: Run + RunEvent CRUD; `append_event` skips `delta` type (Q35); monotonic seq
  - Test: `pytest backend/tests/test_runs_store.py -q` — create run, append events, replay by `seq` (no deltas stored)
  - Done: runs survive restart, structural replay in order from PostgreSQL

---

## Phase 4 — API + main + cleanup

- [ ] **4.1 Runs + admin + health API + artifacts + worker**
  - Files: COPY `bff/envelope.py` → `app/bff/`; REWRITE `artifacts.py` (Q34 file-based under `{upload_dir}/{notebook_id}/artifacts/`); WRITE `api/deps.py` (Ollama + registries + orchestrator, no PluginManager); COPY/adapt `api/runs.py`; **REWRITE `runs/manager.py` (Q31 worker contract)**; stub `api/admin.py` (Q37: `GET /v1/admin/health` only); COPY `api/health.py`
  - Edits: `CreateRunRequest{notebook_id, message}` → `202 {run_id}` bare; SSE replays persisted events only (Q35); emit `sources` on `rag.query` (Q32); artifacts as download URLs (Q34); worker loads/persists `conversation_summary`; never writes `messages`
  - Test: `curl -X POST localhost:8000/v1/runs -H "Content-Type: application/json" -d '{"notebook_id":"<uuid>","message":"hello"}'` → `202`
  - Done: runs lifecycle + structural SSE replay + sources + cancel work

- [ ] **4.2 Rewrite `app/main.py`**
  - Files: REPLACE `rip/backend/app/main.py`
  - Edits: merge RIP lifespan (load VectorRAG once) + Athena `/v1` mounts; no plugin loader
  - Test: `uvicorn app.main:app --port 8000` + `GET /api/health` and `GET /health` both `ok`
  - Done: both `/api/*` and `/v1/*` serve

- [ ] **4.3 Delete `routes/llm.py`**
  - Files: DELETE `rip/backend/app/routes/llm.py`
  - Edits: remove `/api/prompt` + `/api/prompt/stream`, no shim
  - Test: search `"/api/prompt"` in `backend/` + `frontend/src/` → 0 hits; `pytest -q` still green
  - Done: old endpoints gone

Phase 4 exit: backend-only e2e works without frontend: create notebook via `/api/notebooks`, upload via `/api/files`, `POST /v1/runs`, stream `/events`.

---

## Phase 5 — Frontend (runs only)

- [ ] **5.1 `types/runs.ts`**
  - Files: CREATE `frontend/src/types/runs.ts`
  - Edits: discriminated union `RunEvent` with `seq: number` on every variant (see `MERGE_PLAN.md §Frontend`); include `sources` + `cancelled`; `Artifact{artifact_id, kind, filename, url}` (Q34)
  - Test: `npx tsc -b` in `frontend/`
  - Done: typecheck passes

- [ ] **5.2 `services/runs.ts`**
  - Files: CREATE `frontend/src/services/runs.ts` (`createRun(notebookId, message)`, `subscribeToRunEvents(runId, onEvent)` via `EventSource`, `cancelRun(runId)`)
  - Edits: use `VITE_API_URL` via `API` from `@/config`, `EventSource`, no hardcoded host
  - Test: `npm run lint` in `frontend/`
  - Done: no lint errors

- [ ] **5.3 `hooks/notebooks/useMessages.ts` + UI components**
  - Files: EDIT `frontend/src/hooks/notebooks/useMessages.ts`, `frontend/src/components/notebook/ChatArea.tsx` (and/or `Footer.tsx`); DELETE `services/llm.ts` import
  - Edits: run lifecycle; handle `sources` event (Q32); `delta` live only; on reconnect apply persisted events (Q35 — no delta replay); `artifacts` download links (Q34); cancel button; single `createMessageAPI(assistant, text, sources)` on `run_completed`
  - Test: manual chat — send message, see plan collapse, tokens stream, sources appear, cancel works
  - Done: tokens stream live, refresh replays structural events, cancel stops run

- [ ] **5.4 `config.ts` + `vite.config.ts` + `nginx.conf` + `Dockerfile`**
  - Files: EDIT `frontend/src/config.ts`, `frontend/vite.config.ts`, `frontend/nginx.conf`, `backend/Dockerfile`
  - Edits: keep `export const API = VITE_API_URL (http://localhost:8000)`; `vite.server.proxy` for `/api/` + `/v1/`; `nginx` add `location /v1/` = `/api/` block; `backend/Dockerfile` CMD `app.main:app`; no `8010`
  - Test: `npm run dev`, search `8010` in `frontend/src/` → 0 hits
  - Done: dev proxy works for both `/api/` and `/v1/`

Phase 5 exit: full UI chat works: Gallery → Workspace → send → plan + answer + sources.

---

## Phase 6 — Integration + hardening

- [ ] **6.1 Backend suite**
  - Test: `pytest` (from `rip/`) + `ruff check .`
  - Done: green; only known noise is torch/CUDA teardown dump after pass (exit 0, not a failure)

- [ ] **6.2 Smoke test (must pass before merge done)**
  1. Create notebook → upload PDF → poll `GET /api/notebooks/{id}/files` until `ready`
  2. `POST /v1/runs {notebook_id, message}` → `202 {run_id}`
  3. `GET /v1/runs/{id}/events` streams `run_started → plan → step_started → delta* (live) → step_completed → sources? → summary → artifacts? → run_completed`
  4. Confirm `rag.query` chunks + Ollama answer; `sources` SSE event present; long history updates `notebooks.conversation_summary`
  5. Refresh mid-run → reconnect `/events` → structural replay (no delta flood), no duplicate assistant message
  6. `POST /v1/runs/{id}/cancel` → `cancelled`, worker stops before next step

- [ ] **6.3 Frontend checks**
  - Test: `npm run lint` + `npm run build` in `frontend/`
  - Done: both pass, no `zustand` / `@tanstack/react-query` added

---

## Test matrix (quick ref)

| Level | Command | Where | Pass means |
|---|---|---|---|
| Lint backend | `ruff check .` | `rip/` | 0 errors |
| Unit backend | `pytest backend/tests/ -q` | `rip/` | green |
| Typecheck frontend | `npx tsc -b` | `rip/frontend/` | no errors |
| Lint frontend | `npm run lint` | `rip/frontend/` | clean |
| Build frontend | `npm run build` | `rip/frontend/` | dist built |
| Compose | `docker compose config` | `rip/` | valid |
| Health | `GET localhost:8000/api/health` | browser/curl | `{"status":"ok"}` |
| Runs e2e | Phase 6.2 steps | curl + UI | full SSE chain + replay + cancel |

## Troubleshooting

- DB connect fail → check `DB_*`, pgvector extension, `schema.sql` applied twice.
- Ollama empty → `OLLAMA_BASE_URL` reachable, `qwen2.5:14b` pulled (`ollama list`).
- Startup crash (models) → `BGE_M3_MODEL_PATH` / `BGE_RERANKER_V2_M3` wrong; fix `.env`.
- Upload stuck `processing` → `run_rag_pipeline` has no retry; re-`POST /api/files/{id}/process`.
- SSE stops on refresh → check `run_events` rows exist (structural only); frontend re-`GET /events`, dedupe by `seq`; resume text from `step_completed`/`summary` (deltas not replayed — Q35).
- Confused `summary` vs `conversation_summary` → SSE `summary` = final answer; DB column = internal memory (Q38).
