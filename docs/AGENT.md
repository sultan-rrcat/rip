# Maintainer Guide (`AGENT.md`)

Conventions for working in this repo. Design: `ARCHITECTURE.md`.

---

## 1. Principles

- **Privacy & offline-first.** No telemetry or external SaaS. Ollama + local weights only; Langfuse strictly opt-in.
- **One concern per change.** Small, reviewable commits; never mix refactor, behavior, and infra in one commit.
- **No guessing.** Ask on architectural ambiguity. Never invent endpoints, config keys, or dependencies.
- **Record decisions.** Architecture changes get an `ADR.md` entry.

## 2. Module map (`backend/app/` root)

```
main.py              lifespan (VectorRAG) + /v1 mounts
core/config.py       Pydantic Settings (port 8000, OLLAMA_*)
core/db.py, dependencies.py, logging.py
rag/pipeline.py, vector_rag.py
routes/notebooks.py, files.py, messages.py, auth.py
services/chat.py, file_processor.py
providers/base.py, ollama.py, streaming.py, tracing.py
agents/base.py, registry.py, reasoning.py
tools/base.py, registry.py, executor.py, rag_query.py, notebook_inspect.py,
  plot_chart.py, doc_generate.py, doc_convert.py
orchestration/plan.py, planner.py (thin holder, no DAG prompt), router.py,
  intents.py, builders.py, react.py (shim over react_engine), react_engine.py,
  idle_guard.py, corpus.py, validator.py, aggregator.py,
  engine.py, plan_graph.py, orchestrator.py, memory.py, results.py
store/runs.py        Postgres CRUD for runs + run_events (no deltas)
runs/manager.py      worker: memory, sources, never writes messages
artifacts.py         file-based artifacts
bff/envelope.py      SSE event vocabulary
api/deps.py          runtime wiring (no plugin system)
api/runs.py, admin.py (health stub), health.py
observability/langfuse.py
frontend/src/
  services/*, types/*, hooks/notebooks/*, pages/, components/
```

## 3. Coding & style

- **Backend:** strict type hints, `from app.*` imports, DI via `core/dependencies.py`, async I/O, `asyncio.to_thread` for ML. Config in `core/config.py` only. Ruff 88/py311.
- **Frontend:** functional components + hooks. API in `services/*`, data in `hooks/*`, no direct `fetch` in components. Tailwind + MUI v9 default theme. Explain *why*, not *what*.
- **Deps:** backend root `pyproject.toml` (no `requirements.txt`); frontend `package.json`. New dependencies need justification + ADR note. Before changing, updating, or dropping any dependency that forces `pip install` again, you must reconfirm with the user.

## 4. Workflow

1. Pick one task. Implement with tests.
2. Run `pytest` (repo root) + `ruff`; frontend: `npm run lint` + `npm run build`.
3. Inspect `git status` + `git diff --stat`; stage only intended paths (never `git add -A` across scopes).
4. Commit with conventional prefix (`fix(backend): …`, `feat(frontend): …`, `docs: …`, `chore(deps|docker|config): …`, `test(backend): …`).
5. Push only when explicitly requested.

## 5. Commands

| Task | Command | Where |
|---|---|---|
| Install backend | `pip install -e .` | root |
| Run backend | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` | `backend/` |
| Backend tests | `pytest` | root |
| Backend lint | `ruff` | root |
| Frontend install/dev/lint/build | `npm install` / `npm run dev` / `npm run lint` / `npm run build` | `frontend/` |
| Compose lifecycle | `scripts/rip.ps1 <up\|down\|fresh\|restart\|rebuild\|logs\|ps\|status\|migrate\|health\|help>` (`.sh` mirror on Linux/macOS; host remaps via `HOST_*_PORT`) — canonical; `up` rebuilds by default (`VITE_API_URL` bake); raw `docker compose` only for one-offs | root |

## 5.1 Host environments (`agent_env` vs `ml_env`)

Two machine-specific host envs — not interchangeable by preference. Use whichever exists on the machine you're on.

- **Office** → `agent_env` at `C:\Users\trainee\ENV\agent_env\`
- **Home** → `ml_env` at `C:\Users\offic\venvs\ml_env` (probed 2026-09-14: Python 3.12.0, torch 2.14.0+cpu)

**Interpreters:**
```
Office: C:\Users\trainee\ENV\agent_env\Scripts\python.exe -m pytest
Home: C:\Users\offic\venvs\ml_env\Scripts\python.exe -m pytest
```

**Ollama (host-local, per machine):**
```dotenv
# Home
OLLAMA_BASE_URL=http://localhost:11434

# Office
OLLAMA_BASE_URL=http://10.10.30.77:21434
```


## 6. Standing rules (do not break)

- Ollama only (`OLLAMA_BASE_URL`); port `8000`; `CORS_ORIGINS` plain str (`*` = no credentials; explicit list required with cookie auth).
- Login first (`/api/auth/*`, cookie `rip_session`); all `/api/*` + `/v1/*` except health require auth; rows owner-scoped (404-on-foreign).
- `/v1/*` bare JSON; no envelope. Frontend owns `messages`; backend never writes them.
- `delta` live-only; `conversation_summary` ≠ SSE `summary`; sources via SSE after `rag.query`; artifacts as disk URLs.
- `notebook_id` injected by the engine, never LLM-generated. No query rewrite. Admin health stub only.
- Tests live in `backend/tests/`. In-compose backend uses `DB_HOST=postgres:5432`; host-local uses `127.0.0.1` (see CAVEATS).
- Dependencies are locked: before changing, updating, or dropping any dependency that forces `pip install` again, you must reconfirm with the user.
