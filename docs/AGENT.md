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
tools/base.py, registry.py, executor.py, rag_query.py, plot_chart.py,
  doc_generate.py, code.sandbox.py, image_generate.py
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

## 6. Standing rules (do not break)

- Ollama only (`OLLAMA_BASE_URL`); port `8000`; `CORS *` as plain str.
- `/v1/*` bare JSON; no envelope. Frontend owns `messages`; backend never writes them.
- `delta` live-only; `conversation_summary` ≠ SSE `summary`; sources via SSE after `rag.query`; artifacts as disk URLs.
- `notebook_id` injected by the engine, never LLM-generated. No query rewrite. Admin health stub only.
- Tests live in `backend/tests/`. In-compose backend uses `DB_HOST=postgres:5432`; host-local uses `127.0.0.1` (see CAVEATS).
