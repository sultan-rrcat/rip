# Session Log (`SESSION_LOG.md`)

## [2026-09-08] - Documentation suite initialization
- **Task:** Analyze codebase and create the initial documentation suite under the "open-source local NotebookLM" philosophy.
- **Actions Taken:** Analyzed repo structure, backend services/routes, and React frontend. Created `ARCHITECTURE.md`, `SETUP.md`, `AGENT.md`, `ADR.md`, `PLAN.md`, and this `SESSION_LOG.md`.
- **Status:** Completed.

---

## [2026-09-08] - Documentation rewrite (grounded in code analysis)
- **Task:** Rewrite `Readme.md` and the `docs/` suite so every claim matches the actual code (no guessing).
- **Actions Taken:**
  - Re-verified the codebase (backend `config.py`, `app.py`, `core/`, `rag/`, `routes/`, `services/`, `schema.sql`; frontend `pages/`, `components/`, `hooks/`, `services/`, `config.js`).
  - Rewrote `Readme.md` as a layered overview (what it is, status, true stack/models, layout, quickstart, doc index).
  - Rewrote `SETUP.md` to use the real env vars (`LLM_URL`, `DB_*`, `NEO4J_*`), the root `pyproject.toml` install (no `requirements.txt`), and manual DB bootstrap with the pgvector extension.
  - Rewrote `ARCHITECTURE.md` with the actual topology, data model, ingestion/retrieval flows, the full HTTP API surface, and a new **Known Issues & Deviations** section (#1–#10).
  - Rewrote `AGENT.md` with the verified repo map, commands, and "known traps" (env-var split, port mismatch, hardcoded model paths, agentic stub).
  - Rewrote `ADR.md`: corrected ADR-001/002/003 to reality, added ADR-004 (Docling/header chunking), ADR-005 (background-task ingestion), ADR-006 (deferred Agentic RAG).
  - Rewrote `PLAN.md` to a status-true roadmap whose fix items map to the Known Issues list.
- **Notable corrections vs. the prior docs:** LLM is `qwen2.5-coder-14b` over OpenAI-compatible HTTP (not "qwen2.5-14B"); active embedder is BGE-M3 (not all-MiniLM-L6-v2); no `requirements.txt` and no `DATABASE_URL`; the active RAG is `GraphRAG`; `AgenticRAG` is an unused stub; several frontend bugs recorded.
- **Non-goals (per instructions):** no code changes made; bugs are documented only, not fixed.
- **Status:** Completed successfully.

---

## [2026-09-08] - Bug fixes from Known Issues list
- **Task:** Fix the bugs documented in the Known Issues & Deviations section; no new features.
- **Actions Taken:**
  - **Known Issue #1 (env-var split):** removed the unused `LLM_API_URL` read (and the now-unused `os` import) from `backend/app.py`; all LLM calls now use `config.LLM_URL` (`LLM_URL`).
  - **Known Issue #2 (`/api/prompt` signature):** `backend/routes/llm.py` now passes `request.notebook_id` to `VectorRAG.retrieve_context(...)`.
  - **Known Issue #3 (port mismatch):** `frontend/src/hooks/notebooks/useFiles.js` `/process` call now uses `API` from `src/config.js` instead of hardcoded `localhost:5000`.
  - **Known Issue #4 (broken upload error path):** `frontend/src/services/files.js` `uploadFileAPI` error branch now throws instead of referencing undefined `uuidv4`/`setFiles`.
  - **Known Issue #5 (wrong id):** `frontend/src/hooks/notebooks/useMessages.js` error branch now uses `notebook_id` instead of bare `id`.
  - Updated `docs/ARCHITECTURE.md` (marked #1–#5 fixed), `docs/PLAN.md` (checked off those fix items), and `docs/AGENT.md` (refreshed "known traps").
- **Notes:** tests/lint were not run per user instruction. Pre-existing unused imports (`VectorRAG`, `httpx` in `app.py`) and Known Issues #6–#10 (dead code, hardcoded model paths, unmounted UI, test coverage, legacy naming) remain open.
- **Status:** Completed successfully.
