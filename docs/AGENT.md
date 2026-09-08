# Agent & Vibe Coding Guidelines (`AGENT.md`)

Guidance for human and AI-agent contributors working in this repository. This file is kept in sync with the actual code — the folder map and commands below are verified.

---

## 1. Core principles

- **Privacy & offline-first.** Never add dependencies or code that sends telemetry, logs, or user documents to external cloud services. Everything runs locally or against the internal `LLM_URL`. The whole point of this project is that it stays air-gapped.
- **Fidelity to the real layout.** Respect the separation:
  - `backend/routes/` — HTTP endpoints only (thin handlers).
  - `backend/services/` — business logic (chat, file processing, LLM calls, query rewriting).
  - `backend/rag/` — retrieval & ingestion engine.
  - `backend/core/` — DB connections, logging, FastAPI deps, prompt templates.
  - `frontend/src/components/`, `pages/`, `hooks/`, `services/` — keep API calls in `services/` and data-fetching logic in `hooks/`.
- **No guessing.** When facing architectural ambiguity, missing requirements, or library choices, **ask the user** rather than guessing. This is a hard rule.
- **Document reality, not intention.** If behavior drifts from the docs, update the docs (and the Known Issues list in `docs/ARCHITECTURE.md`) rather than hiding the drift.

---

## 2. Coding & style conventions

- **Backend (Python):** strict type hints; follow the existing FastAPI dependency-injection pattern (`core/dependencies.py`); use async where I/O-bound; blocking ML work is offloaded with `asyncio.to_thread`. Config goes in `backend/config.py` / env, not scattered in files. Ruff config is in `pyproject.toml` (line length 88, target py311).
- **Frontend (React):** clean functional components with hooks. Styling is Tailwind utility classes plus MUI v7 components/icons (MUI uses its default theme — do not introduce a `ThemeProvider` unless asked). API access goes through `src/services/*` and is consumed by `src/hooks/*`; components should not `fetch` directly.
- **Comments:** explain *why*, not *what*. No conversational commentary. (Note: much existing code has emoji/banner comments — don't propagate that style into new code unless asked.)
- **Dependencies:** backend deps live in the root `pyproject.toml` (there is **no** `requirements.txt`). Frontend deps in `frontend/package.json`.

---

## 3. Repository map (current, verified)

```
backend/
  app.py            FastAPI app; mounts routes; lifespan loads VectorRAG
  config.py         upload dir, model paths (D:\models\...), LLM_URL
  schema.sql        Postgres schema (notebooks, files, messages, embeddings_test)
  core/             db.py, dependencies.py, logging.py
  rag/              pipeline.py (ingestion), vector_rag.py (active), base.py (unused)
  routes/           notebooks.py, files.py, messages.py, llm.py
  services/         chat.py, file_processor.py, llm.py, rewritter.py (note spelling)
  tests/            test_app.py
frontend/src/
  pages/            Home.jsx, Notebook.jsx
  components/       home/Card.jsx; notebook/LeftSidebar, ChatArea, Footer (mounted);
                    notebook/Header, RightSidebar, Main, Notification (NOT mounted)
  hooks/notebooks/  useNotebook.js, useMessages.js, useFiles.js
  services/         notebooks.js, messages.js, files.js, llm.js
  config.js         backend base URL (http://localhost:8000)
pyproject.toml      backend manifest + ruff + pytest
.env.example        env template
```

---

## 4. Git workflow

Follow the established feature-branch workflow:

```bash
git checkout main
git pull origin main
git checkout -b feature/<feature-name>
# make changes, test, commit
git add .
git commit -m "descriptive message"
git push --set-upstream origin feature/<feature-name>
git rebase main
git checkout main
git merge feature/<feature-name>
git push origin main
```

---

## 5. Commands

| Task | Command | Where |
|---|---|---|
| Install backend | `pip install -e .` | repo root |
| Run backend | `uvicorn app:app --host 0.0.0.0 --port 8000 --reload` | `backend/` |
| Backend tests | `pytest` | repo root |
| Backend lint | `ruff` | repo root |
| Frontend install | `npm install` | `frontend/` |
| Frontend dev | `npm run dev` | `frontend/` |
| Frontend lint | `npm run lint` | `frontend/` |
| Frontend build | `npm run build` | `frontend/` |

---

## 6. Known traps (read before editing)

- **Env vars:** all LLM calls read `LLM_URL` (from `config.py`); set `LLM_URL`. (The old unused `LLM_API_URL` read in `app.py` was removed.)
- **Ports:** backend runs on `8000` (per `frontend/src/config.js`); the `/process` trigger now uses that same base URL. Keep API calls routed through `src/config.js` rather than hardcoding ports.
- **Model paths are hardcoded** to `D:\models\...` in `config.py`; changing them affects ingestion/startup.
- **`BaseRAG` is unused** — the concrete RAG classes extend `RagPipeline` directly; don't build on `BaseRAG` as if it's wired in.
- **Retrieval:** both `/api/prompt` and `/api/prompt/stream` use `VectorRAG` (the graph RAG stack was removed; see ADR-007).
- When you fix a bug from the Known Issues list, move/annotate it in `docs/ARCHITECTURE.md` and `docs/PLAN.md` accordingly.
