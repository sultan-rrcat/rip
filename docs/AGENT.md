# Agent & Vibe Coding Guidelines (`AGENT.md`)

> Merge mode: `docs/MERGE_PLAN.md` is authoritative. Read it §0→API fully before any Athena→RIP work. On conflict, MERGE_PLAN wins.

---

## 1. Core principles

- **Privacy & offline-first.** No telemetry or external SaaS. Ollama + local weights only.
- **One box per session.** Implement exactly one unchecked Day-1 box from MERGE_PLAN Implementation Order. No parallel half-edits.
- **No guessing.** Ask on architectural ambiguity. Never invent endpoints, config keys, or deps.
- **Document reality.** Append `SESSION_LOG.md`; append MERGE_PLAN Decision Log for deviations (new ADR entry). Never silently rewrite Decisions/Inavariants.

## 2. Target layout (Day-1, `backend/app/` root)

```
backend/app/
  main.py              lifespan (VectorRAG) + /v1 mounts
  core/config.py       Pydantic Settings (port 8000, OLLAMA_*)
  core/db.py, dependencies.py, logging.py
  rag/pipeline.py, vector_rag.py
  routes/notebooks.py, files.py, messages.py   # llm.py DELETED
  services/chat.py, file_processor.py, rewritter.py
  providers/base.py, ollama.py                 # Day-1
  agents/base.py, registry.py, reasoning.py    # coding/vision Phase 2
  tools/base.py, registry.py, executor.py, rag_query.py  # rest Phase 2
  orchestration/plan.py, planner.py, validator.py, aggregator.py,
    engine.py, plan_graph.py, orchestrator.py, memory.py, results.py
  store/conversations.py  # Postgres, notebook-scoped
  runs/manager.py         # ephemeral
  bff/envelope.py
  api/runs.py, conversations.py, admin.py (3 endpoints), health.py
frontend/src/
  services/runs.ts, types/runs.ts
  hooks/notebooks/useMessages.ts  # useState+EventSource, no zustand/query
```

## 3. Coding & style

- **Backend:** strict type hints, `from app.*` imports, DI via `core/dependencies.py`, async I/O, `asyncio.to_thread` for ML. Config in `core/config.py` only. Ruff 88/py311.
- **Frontend:** functional components + hooks. API in `services/*`, data in `hooks/*`, no direct `fetch` in components. Tailwind + MUI v7 default theme. Explain *why*, not *what*.
- **Deps:** backend root `pyproject.toml` (no `requirements.txt`); frontend `package.json`. Day-1 adds none.

## 4. Session ritual

Start: read MERGE_PLAN §0→API, pick one Day-1 box.
End: `pytest` + `ruff`, check box with commit SHA, append `SESSION_LOG.md` (`## [date] - MERGE Box #N - prompt/commit/status`).

## 5. Commands

| Task | Command | Where |
|---|---|---|
| Install backend | `pip install -e .` | root |
| Run backend | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` | `backend/` |
| Backend tests | `pytest` | root |
| Backend lint | `ruff` | root |
| Frontend install/dev/lint/build | `npm install` / `npm run dev` / `npm run lint` / `npm run build` | `frontend/` |

## 6. Known traps (Day-1)

- **Ollama, not `LLM_URL`.** Set `OLLAMA_BASE_URL`, model `qwen2.5:14b`.
- **Port `8000`.** No `8010`. Frontend `VITE_API_URL`, vite proxies `/v1/` only.
- **`/api/prompt[/stream]` gone.** Use `POST /v1/runs` + SSE. Keep `/api/health` alias.
- **`messages.conversation_id` nullable.** `NULL` = pre-merge history; new writes set `notebook_id + conversation_id`.
- **Summary only when >10 turns.** Don't force per-request summarization.
- **Test teardown dump** (torch/CUDA access-violation after pass, exit 0) is not a failure.
