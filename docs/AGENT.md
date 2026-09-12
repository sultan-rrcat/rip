# Agent & Vibe Coding Guidelines (`AGENT.md`)

> Merge mode: `docs/MERGE_PLAN.md` is authoritative. Read it §0→API fully before any Athena→RIP work. On conflict, MERGE_PLAN wins.

---

## 1. Core principles

- **Privacy & offline-first.** No telemetry or external SaaS. Ollama + local weights only.
- **One box per session.** Implement exactly one unchecked box from MERGE_PLAN Implementation Order. No parallel half-edits.
- **No guessing.** Ask on architectural ambiguity. Never invent endpoints, config keys, or deps.
- **Document reality.** Append `SESSION_LOG.md`; append MERGE_PLAN Decision Log for deviations (new ADR entry). Never silently rewrite Decisions/Inavariants.

## 2. Target layout (`backend/app/` root)

```
backend/app/
  main.py              lifespan (VectorRAG) + /v1 mounts
  core/config.py       Pydantic Settings (port 8000, OLLAMA_*)
  core/db.py, dependencies.py, logging.py
  rag/pipeline.py, vector_rag.py
  routes/notebooks.py, files.py, messages.py   # llm.py DELETED
  services/chat.py, file_processor.py          # rewritter.py + llm.py DELETED (Q33)
  providers/base.py, ollama.py, streaming.py
  agents/base.py, registry.py, reasoning.py, coding.py, vision.py
  tools/base.py, registry.py, executor.py, rag_query.py, plot_chart.py, doc_generate.py, code_sandbox.py, image_generate.py
  orchestration/plan.py, planner.py, validator.py, aggregator.py,
    engine.py, plan_graph.py, orchestrator.py, memory.py, results.py
  store/runs.py          # Postgres CRUD for runs + run_events (no deltas — Q35)
  runs/manager.py        # REWRITE worker (Q31): memory, sources, never write messages
  artifacts.py           # file-based artifacts (Q34)
  bff/envelope.py        # standard wrapper {data, error}
  api/deps.py            # wire Ollama + registries (no PluginManager)
  api/runs.py, admin.py (health stub only), health.py
frontend/src/
  services/runs.ts, types/runs.ts
  hooks/notebooks/useMessages.ts  # useState+EventSource
```

## 3. Coding & style

- **Backend:** strict type hints, `from app.*` imports, DI via `core/dependencies.py`, async I/O, `asyncio.to_thread` for ML. Config in `core/config.py` only. Ruff 88/py311.
- **Frontend:** functional components + hooks. API in `services/*`, data in `hooks/*`, no direct `fetch` in components. Tailwind + MUI v7 default theme. Explain *why*, not *what*.
- **Deps:** backend root `pyproject.toml` (no `requirements.txt`); frontend `package.json`. No new deps.

## 4. Session ritual

Start: read MERGE_PLAN §0→API, pick one box.
End: `pytest` + `ruff`, check box with commit SHA, append `SESSION_LOG.md` (`## [date] - MERGE Box #N - prompt/commit/status`).

### 4b. Multi-stage commits (after each phase box)

Commit the box in reviewable stages, smallest scope first. Never one giant commit. Order:

1. `refactor(backend): Phase X.Y <structural change>` — moves/copies/deletes + import fixes only.
2. `test(backend|frontend): Phase X.Y <test migration>` — conftest/tests, no source changes.
3. `chore(docker|config): Phase X.Y <infra change>` — Dockerfile/compose/env, if any.
4. `docs(merge): Phase X.Y <log + checkbox>` — SESSION_LOG entry + IMPLEMENTATION_PLAN checkbox (+ workflow doc updates, if any).

Rules: inspect `git status` + `git diff --stat` before each stage; stage only intended paths (`git add <paths>`, never `git add -A` across scopes); one concern per commit; each message ends with the phase ref (e.g. `Phase 1.1`); push only when explicitly requested.

## 5. Commands

| Task | Command | Where |
|---|---|---|
| Install backend | `pip install -e .` | root |
| Run backend | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` | `backend/` |
| Backend tests | `pytest` | root |
| Backend lint | `ruff` | root |
| Frontend install/dev/lint/build | `npm install` / `npm run dev` / `npm run lint` / `npm run build` | `frontend/` |

## 6. Known traps

- **Ollama, not `LLM_URL`.** Set `OLLAMA_BASE_URL`, model `qwen2.5:14b`.
- **Port `8000`.** No `8010`. Frontend `VITE_API_URL`, vite proxies `/api/` + `/v1/`, nginx proxies both too.
- **`/api/prompt[/stream]` gone.** Use `POST /v1/runs {notebook_id,message}` + `EventSource /events`. Keep `/api/health` alias. No envelope on `/v1/*`.
- **`messages` owned by frontend.** Backend `runs/manager` never writes `messages`; dedupe SSE by `seq`.
- **`conversation_summary` ≠ SSE `summary`.** DB column = internal memory compression; SSE `summary` = final answer text (Q38).
- **`delta` not replayed.** Persist structural events only (Q35); reconnect uses `step_completed`/`summary` for text.
- **No query rewrite.** `rewritter.py` dropped (Q33); Planner handles retrieval intent.
- **Sources via SSE.** `{type:"sources"}` after `rag.query` (Q32); not a separate pre-RAG call.
- **Artifacts on disk.** File URLs in SSE, not inline base64 (Q34).
- **Admin health stub only.** No `/v1/admin/plugins` or `/reload` (Q37).
- **Runs persist to Postgres.** Structural events in `run_events`; only stop button terminates.
- **Import map + workdir.** `routes.→app.routes.` etc.; run from `backend/` as `app.main:app`; Dockerfile CMD `app.main:app`.
- **Config aliases.** Support `BGE_MODEL_DIR`/`RERANKER_MODEL_DIR`; `cors_origins: str`.
- **Test path.** `backend/tests/` (not `backend/app/tests/`). Conftest migrates in Phase 1.1.
- **Test teardown dump** (torch/CUDA access-violation after pass, exit 0) is not a failure.

## 7. Missing best-practices checklist (vibe-tool must not skip)

- `backend/Dockerfile` CMD + `frontend/nginx.conf` `/v1/` block + `vite.config.ts` proxy (see MERGE_PLAN §Frontend).
- `backend/tests/test_providers|tools|orchestration|runs_store.py` — real DB + real Ollama; mock only if Ollama down.
- Artifacts under `{UPLOAD_DIR}/{notebook_id}/artifacts/`, served as download links from SSE `artifacts` event (Q34).
- `BGE`/`reranker` paths + `sandbox_image` pull (`docker pull python:3.11-slim`) before Phase 3.1.
- Run worker contract (Q31) — do not copy Athena `manager.py` verbatim.
