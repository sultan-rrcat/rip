# RIP — Research Intelligence Platform

Offline research assistant: upload documents into notebooks, then chat over them. Document RAG (Docling + BGE-M3 + Postgres/pgvector) meets multi-agent orchestration (LangGraph + Ollama) with tool execution — all local, no cloud required.

---

## What it does

- **Notebooks** group documents + chat history (one notebook = one conversation).
- **Uploads** are parsed (Docling), chunked by Markdown headers, embedded (BGE-M3), and stored in Postgres+pgvector.
- **Chat** runs through `POST /v1/runs` + SSE (`run_started → plan → step_started → delta → step_completed → sources → summary → run_completed`) with reasoning, coding, and vision agents plus five tools: `rag.query`, `plot.chart`, `doc.generate`, `code.sandbox`, `image.generate`.
- **Runs persist** to Postgres: they survive page refresh, replay their event log on reconnect, and can be cancelled with the stop button.

---

## Tech stack

| Role | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, LangGraph, `backend/app/` |
| Retrieval | `VectorRAG`: vector similarity + full-text rank fusion + BGE rerank |
| LLM | Ollama (`OLLAMA_BASE_URL`, default model `qwen2.5:14b`) |
| DB | Postgres + pgvector (`notebooks/files/embeddings/messages/runs/run_events`) |
| Frontend | React 19 + Vite, Tailwind + MUI v7, `useState` + `EventSource` |
| Observability | Langfuse, opt-in only |

---

## Layout

```
backend/app/   main.py, core/, rag/, routes/, services/, providers/, agents/,
               tools/, orchestration/, store/runs.py, runs/, bff/, api/,
               observability/, artifacts.py
frontend/src/  pages/, components/, hooks/notebooks/, services/, types/
docs/          ARCHITECTURE.md, SETUP.md, CAVEATS.md, ADR.md, AGENT.md,
               CONTEXT.md, CHANGELOG.md, archive/
```

---

## Quickstart

1. Read `docs/SETUP.md` (environment, database, Ollama, compose).
2. `copy .env.example .env`, then `docker compose up postgres backend frontend`.
3. Frontend dev: `cd frontend && npm install && npm run dev`.
4. Backend dev: `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`.

Health: `GET http://localhost:8000/api/health` → `{"status":"ok"}`.

---

## Documentation

| Document | Purpose |
|---|---|
| `docs/ARCHITECTURE.md` | System design: topology, modules, data model, run lifecycle, RAG, memory |
| `docs/SETUP.md` | Runnable setup: env, DB bootstrap, compose, smoke test |
| `docs/CAVEATS.md` | Known traps and environment gotchas (read before debugging) |
| `docs/ADR.md` | Architecture decisions, past and present |
| `docs/AGENT.md` | Maintainer guide: conventions, commands, workflow |
| `docs/CONTEXT.md` | Domain glossary and data-flow reference |
| `docs/CHANGELOG.md` | Notable changes going forward |
| `docs/archive/` | Frozen merge-era history (not authoritative) |

---

## Contributing

Feature branches, one concern per commit. Run backend tests (`pytest` from repo root) and `ruff` before committing; keep `docs/CHANGELOG.md` updated for user-visible changes. Decisions that change architecture need an entry in `docs/ADR.md`.
