# RIP — Research Intelligence Platform (Athena→RIP Merge)

Single-codebase, offline research assistant: document RAG + multi-agent orchestration + tool execution.

> **Single point of truth: `docs/MERGE_PLAN.md`.** Start every vibe session there (§0 Authority + session starter). This README is an index only.

---

## What it does (Day-1 target)

- Notebooks group documents + chat history (one notebook = one conversation). Summary stored on notebook (internal, context-window compression).
- Uploads parsed (Docling), chunked, embedded (BGE-M3), stored in Postgres+pgvector.
- Chat goes through `POST /v1/runs` + SSE (`run_started→plan→delta→summary→run_completed`) with reasoning agent + `rag.query`. Runs persist to Postgres; survive page refresh; full SSE replay on reconnect.
- Phase 2 (deferred): coding/vision agents, `plot.chart`, `doc.generate`, `code.sandbox`, `image.generate`, Redis, Langfuse container.

Pre-merge prototype (VectorRAG notebook Q&A) works; merge replaces `/api/prompt[/stream]` with `/v1/runs`.

---

## Tech stack (target)

| Role | Choice |
|---|---|
| Backend | FastAPI 3.11+, LangGraph DAG, `backend/app/` root |
| Retrieval | `VectorRAG`: pgvector cosine + full-text RRF + BGE rerank |
| LLM | Ollama `qwen2.5:14b` (`OLLAMA_BASE_URL`) |
| DB | Postgres+pgvector (`notebooks/files/embeddings/conversations/messages`) |
| Frontend | React 19 + Vite, Tailwind + MUI v7, `useState+EventSource` (no store lib Day-1) |

---

## Layout

```
backend/app/   main.py, core/, rag/, routes/, services/, providers/, agents/,
               tools/, orchestration/, store/runs.py, runs/, bff/, api/
frontend/src/  pages/, components/, hooks/notebooks/, services/runs.ts, types/runs.ts
docs/          MERGE_PLAN.md (master), AGENT.md, SETUP.md, ADR.md, SESSION_LOG.md
```

---

## Quickstart

1. `docs/SETUP.md` (env, DB, Ollama, compose).
2. `docker compose up postgres backend frontend` — Day-1 boot.
3. Frontend dev: `cd frontend && npm install && npm run dev`.

Backend: `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`.

---

## Documentation index (surviving files only)

| Document | Purpose |
|---|---|
| `docs/MERGE_PLAN.md` | **Master spec** — goal, decisions Q1–Q16, contracts, order, logs |
| `docs/SETUP.md` | Runnable env/DB/compose/smoke test |
| `docs/AGENT.md` | Session rules + target map + ritual |
| `docs/ADR.md` | History + merge ADRs 008–013 |
| `docs/SESSION_LOG.md` | Append-only session history |

Deleted as merge-dead: `ARCHITECTURE.md` (salvaged to MERGE Appendix A), `PLAN.md` (open items → MERGE Phase 2), `GITHUB_MIGRATION.md` (ops one-off).

---

## Contributing

Feature-branch workflow. One MERGE_PLAN box per session. Update box + `SESSION_LOG.md`; deviations need ADR entry.
