# RAG Chatbot Assistant (NotebookLM-style)

A RAG chatbot prototype ("RIP") in the style of Google NotebookLM, built for a physics R&D organization. The system is designed for **internal, offline use only**: data, models, and processing stay within the local network.

> Project/repo name: `rip-prototype` · Python backend + React frontend.

---

## What it does

- Create **notebooks** that group uploaded documents and a chat history.
- Upload PDFs, which are parsed (**Docling**), chunked, embedded, and indexed in both a vector store (PostgreSQL + pgvector) and a knowledge graph (Neo4j).
- Ask questions against a notebook; answers are produced from **hybrid retrieval** (vector similarity + full-text + graph traversal) reranked and fed to a local LLM, with sources returned.
- Chat is streamed token-by-token over SSE.

See `docs/ARCHITECTURE.md` for the full picture and `docs/SETUP.md` to run it locally.

---

## Current status (prototype)

Core end-to-end flow works: create notebook → upload PDF → background ingestion → streamed Q&A with source listing. Several rough edges, dead-code paths, and known bugs are tracked in the [Known Issues & Deviations](docs/ARCHITECTURE.md#known-issues--deviations) section and in `docs/PLAN.md`.

---

## Tech stack (as implemented)

### Frontend (`frontend/`)
- React 19 + Vite (dev/build tooling)
- Tailwind CSS v4 (via `@tailwindcss/vite`) for styling
- Material UI v7 + MUI icons for interactive components (default theme)
- React Router v7 · `react-markdown` / `remark-gfm` for chat rendering

### Backend (`backend/`)
- FastAPI (Python 3.11+), async lifespan startup, background-task ingestion
- PostgreSQL with **pgvector** (vector search + full-text via `tsvector`/GIN)
- Neo4j graph database (entity-relationship graph traversal)

### Models
| Role | Model | How it is reached |
|---|---|---|
| LLM (chat, rewriting, entity extraction) | `qwen2.5-coder-14b` | OpenAI-compatible HTTP endpoint at `LLM_URL` (env) |
| Embeddings | `bge-m3` (1024-dim) | Local weights, hardcoded `D:\models\bge-m3` in `backend/config.py` |
| Reranker | `bge_reranker_v2_m3` | Local weights, hardcoded `D:\models\reranker\bge_reranker_v2_m3` |
| Document parsing | Docling | Local library |

---

## Repository layout

```
├── backend/            FastAPI app
│   ├── app.py          App entry, lifespan, router mounting
│   ├── config.py       Model paths, upload dir, env reads
│   ├── schema.sql      PostgreSQL schema (notebooks, files, messages, embeddings)
│   ├── core/           DB connections, logging, FastAPI deps, prompt templates
│   ├── rag/            Retrieval: pipeline, vector_rag, graph_rag (agentic stub)
│   ├── routes/         HTTP endpoints: notebooks, files, messages, llm
│   ├── services/       chat, file_processor, llm, rewritter
│   ├── tests/          Minimal tests
│   └── uploads/        Uploaded files (per-notebook, git-ignored)
├── frontend/           React + Vite SPA
│   └── src/
│       ├── pages/      Home (notebook gallery), Notebook (workspace)
│       ├── components/ Card, LeftSidebar, ChatArea, Footer (+ unmounted Header/RightSidebar)
│       ├── hooks/      useNotebook, useMessages, useFiles
│       └── services/   API clients (notebooks, files, messages, llm)
├── pyproject.toml      Backend dependency manifest + ruff/pytest config
└── .env.example        Environment variable template
```

---

## Quickstart

1. Follow `docs/SETUP.md` (env, Postgres+pgvector, Neo4j, models, dependency install).
2. Backend: `cd backend && uvicorn app:app --host 0.0.0.0 --port 8000 --reload`
3. Frontend: `cd frontend && npm install && npm run dev`

The frontend targets the backend URL defined in `frontend/src/config.js`.

---

## Documentation index

| Document | Audience | Purpose |
|---|---|---|
| `docs/SETUP.md` | Onboarding devs | Exact install, config, and run steps |
| `docs/ARCHITECTURE.md` | Devs + stakeholders | Topology, data flow, API, known issues |
| `docs/AGENT.md` | AI agents / sessions | Working rules + accurate repo map |
| `docs/ADR.md` | Stakeholders + devs | Architecture decision records |
| `docs/PLAN.md` | Devs | Status-true roadmap |
| `docs/SESSION_LOG.md` | Devs | Session history |

---

## Contributing

Follow the git feature-branch workflow in `docs/AGENT.md`. Keep the docs in sync when behavior changes, and update the Known Issues section when bugs are fixed.
