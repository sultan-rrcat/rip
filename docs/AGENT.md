# Maintainer Guide (`AGENT.md`)

Conventions for working in this repo. Design: `ARCHITECTURE.md`. Gotchas: `CAVEATS.md`.

---

## 1. Principles

- **Privacy & offline-first.** No telemetry or external SaaS. Ollama + local weights only; Langfuse strictly opt-in.
- **One concern per change.** Small, reviewable commits; never mix refactor, behavior, and infra in one commit.
- **No guessing.** Ask on architectural ambiguity. Never invent endpoints, config keys, or dependencies.
- **Record decisions.** Architecture changes get an `ADR.md` entry; user-visible changes get a `CHANGELOG.md` entry.

## 2. Module map (`backend/app/` root)

```
main.py              lifespan (VectorRAG) + /v1 mounts
core/config.py       Pydantic Settings (port 8000, OLLAMA_*)
core/db.py, dependencies.py, logging.py
rag/pipeline.py, vector_rag.py
routes/notebooks.py, files.py, messages.py
services/chat.py, file_processor.py
providers/base.py, ollama.py, streaming.py, tracing.py
agents/base.py, registry.py, reasoning.py, coding.py, vision.py
tools/base.py, registry.py, executor.py, rag_query.py, notebook_inspect.py,
  plot_chart.py, doc_generate.py, doc_convert.py, code.sandbox.py, image_generate.py
orchestration/plan.py, planner.py, validator.py, aggregator.py,
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
- **Frontend:** functional components + hooks. API in `services/*`, data in `hooks/*`, no direct `fetch` in components. Tailwind + MUI v7 default theme. Explain *why*, not *what*.
- **Deps:** backend root `pyproject.toml` (no `requirements.txt`); frontend `package.json`. New dependencies need justification + ADR note.

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
| Compose lifecycle | `scripts/rip.ps1 <up\|down\|fresh\|restart\|rebuild\|logs\|ps\|migrate\|health>` (`.sh` mirror on Linux/macOS; host remaps via `HOST_*_PORT`) — canonical; raw `docker compose` only for one-offs | root |

### 5.1 Host environments (`agent_env` vs `ml_env`)

- **Two machine-specific host envs, not interchangeable by preference:**
  - **Office system → `agent_env`**
  - **Home system → `ml_env`** at `C:\Users\offic\venvs\ml_env`
    (probed 2026-09-14: Python 3.12.0, torch 2.14.0+cpu)
- Use whichever interpreter matches the machine you're actually on:
  - Home: `C:\Users\offic\venvs\ml_env\Scripts\python.exe -m pytest`
  - Office: `agent_env`'s interpreter (path TBD — confirm on that machine)
- Old paths (`..\ENV\agent_env`, `C:\Users\offic\ENV\agent_env`) no longer
  resolve on the home machine — `C:\Users\offic\venvs` there contains only
  `ml_env`. This does **not** mean `agent_env` is deprecated; it simply
  doesn't exist on this machine. `docs/archive/SESSION_LOG.md:130` records
  its removal from the home machine specifically.
- When docs/scripts need to reference "the host env" generically, name
  both and let the reader pick based on which machine they're on, rather
  than defaulting to one. Verify presence with
  `Test-Path C:\Users\offic\venvs\ml_env` (home) or the equivalent check
  for `agent_env` (office) before documenting further.
- Host-local DB rule: `DB_HOST=127.0.0.1` (never bare `localhost` on
  Windows) + `DB_PORT` synced to `HOST_PG_PORT`.
- **Host-local Ollama differs by machine:**
  - Home:
```dotenv
    OLLAMA_BASE_URL=http://localhost:11434
```
  - Office:
```dotenv
    OLLAMA_BASE_URL=http://10.10.30.77:21434
```
  - In-compose backend uses `${OLLAMA_BASE_URL:-http://host.docker.internal:11434}`
    via `extra_hosts`: your `.env` value flows into the container, so the
    office LAN remote works with no `extra_hosts`/port adjustment — the
    gateway default is only the fallback when `.env` leaves it unset.
    (Prior open question about the office `21434` mapping: resolved.)

## 6. Standing rules (do not break)

- Ollama only (`OLLAMA_BASE_URL`); port `8000`; `CORS *` as plain str.
- `/v1/*` bare JSON; no envelope. Frontend owns `messages`; backend never writes them.
- `delta` live-only; `conversation_summary` ≠ SSE `summary`; sources via SSE after `rag.query`; artifacts as disk URLs.
- `notebook_id` injected by the engine, never LLM-generated. No query rewrite. Admin health stub only.
- Tests live in `backend/tests/`. In-compose backend uses `DB_HOST=postgres:5432`; host-local uses `127.0.0.1` (see CAVEATS).
