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
- **Status:** Completed successfully.

---

## [2026-09-08] - Remove Graph RAG stack
- **Task:** Remove the Graph RAG features and all traces; make `VectorRAG` the single retrieval path; update docs; delete the stale `prompt.md` artifact.
- **Actions Taken:**
  - Deleted `backend/rag/graph_rag.py` (`GraphRAG` orchestrator), `backend/rag/agentic_rag.py` (stub importing `GraphRAG`), and `backend/core/prompts.py` (`EXTRACTION_PROMPTS`/`DETECTION_PROMPT` only fed graph ingestion).
  - `rag/pipeline.py`: removed `detect_document_type`, `extract_entities`, `extract_entities_async`, `_do_extract`, `store_graph`, and the `__main__` scratch block; cleaned now-unused imports.
  - `core/db.py`: removed `neo4j_driver()` and `NEO4J_*` env reads. `core/dependencies.py`: removed `get_neo4j`; `get_rag` now returns `VectorRAG`.
  - `app.py`: lifespan constructs `VectorRAG()` only; Neo4j driver creation/close removed.
  - `routes/llm.py`: `/api/prompt/stream` now uses `VectorRAG` (`retrieve_context(notebook_id, user_prompt)`) with no Neo4j driver.
  - `routes/files.py` / `routes/notebooks.py`: delete endpoints are Postgres-only (cascade handles embeddings); no graph cleanup or driver.
  - `services/file_processor.py`: ingestion is now load → chunk → embed → store → ready (doc-type detection, entity extraction, graph storage removed).
  - `pyproject.toml`: dropped the `neo4j` dependency (uninstalled from the venv). `.env` / `.env.example`: `NEO4J_*` keys removed.
  - Docs updated: `Readme.md`, `ARCHITECTURE.md` (topology, components, data model, ingestion/retrieval flows, API table, Known Issues #6/#9), `SETUP.md`, `AGENT.md`, `PLAN.md`; `ADR.md`: ADR-002 marked superseded, added ADR-007 recording the removal.
  - Deleted `prompt.md` (historical generation prompt referencing `graph_rag.py`).
- **Notes:** verified with `ruff check` (no new errors vs. pre-existing baseline), `pytest`, and an import smoke test of the FastAPI app. Existing data in the Neo4j database is orphaned; dropping the Neo4j service/database is an ops step outside the repo.
- **Status:** Completed successfully.

---

## [2026-09-11] - PLAN.md remaining-issues sweep
- **Task:** Fix the remaining PLAN.md items (#6 dead code, #7 `.pdf` hardcoding, #8 dead UI, #9 tests, #10 table rename, Phase 0 LLM contract); commit step-by-step.
- **Actions Taken:**
  - Committed pre-existing working-copy changes in two commits (`refactor(backend)` dead-code removal, `chore(docker)` proxy build args).
  - #6: verified `OLLAMA_*`/`BaseRAG`/semantic chunker gone from code; marked Known Issue #6 fixed in `ARCHITECTURE.md`/`PLAN.md`; cleaned `AGENT.md` traps.
  - #7: upload (`routes/files.py`) and ingestion (`services/file_processor.py`) now preserve the real file extension (`.pdf` fallback); marked fixed in docs.
  - #8: deleted `Header.tsx`, `RightSidebar.tsx`, `Main.tsx`, `Notification.tsx`; cleaned `Notebook.tsx` comments; `tsc -b` passes.
  - #10: renamed `embeddings_test` → `embeddings` in `schema.sql` + `pipeline.py` + `vector_rag.py` + docs; migration comment in `schema.sql` header.
  - #9: new 14-test integration suite (`conftest.py` + `test_app.py`) on the real stack — DB/models/APIs, no mocks; prompt tests skip when the LLM is down. Run with the `agent_env` interpreter.
  - Fixes found via testing: `config.py` now accepts `BGE_MODEL_DIR`/`RERANKER_MODEL_DIR` aliases (`.env` names didn't match the `*_PATH` names `config.py` read, so local boot crashed); `schema.sql` FK constraint made re-runnable via a `DO` guard.
  - Phase 0: LLM endpoint + model contract checkbox marked done.
- **Verification:** `pytest` 14/14 PASSED with `agent_env` (exit 0); backend boots via direct `uvicorn` (`/api/health` ok); frontend serves via `npm run dev` (200).
- **Known cosmetic issue:** test runs print a shutdown access-violation dump from native threads after going green (exit code stays 0); noted in `AGENT.md`.
- **Status:** Completed successfully.

---

## [2026-09-11] - Docs consolidation: MERGE_PLAN as single truth (hard rm)

- **Task:** Make `MERGE_PLAN.md` survive vibe sessions; delete merge-dead docs; update helpers + README.
- **Actions Taken:**
  - `MERGE_PLAN.md`: added §0 Authority + session starter + invariants/forbidden list; salvaged ARCHITECTURE chunking/retrieval/ingestion notes to Appendix A; moved PLAN Phase 3 items to Phase 2; added Decision Log Q1–Q16.
  - Deleted (hard `git rm`): `docs/ARCHITECTURE.md`, `docs/PLAN.md`, `docs/GITHUB_MIGRATION.md`.
  - `ADR.md`: marked 002/003/006 superseded by merge; appended ADR-008..013 stubs.
  - `SETUP.md`: rewrote to Day-1 target (Pydantic, Ollama, port 8000, compose, smoke test).
  - `AGENT.md`: rewrote to `backend/app/` target map + merge ritual + traps.
  - `Readme.md`: rewrote to RIP merge index pointing at MERGE_PLAN.
- **Template for next sessions:**
  `## [date] - MERGE Box #N - prompt/commit/status`
- **Status:** Completed (uncommitted working tree).

---

## [2026-09-12] - Merge Docs Review & Hardening Audit

- **Task:** Review and harden the merge plan across all docs (`MERGE_PLAN.md`, `IMPLEMENTATION_PLAN.md`, `ADR.md`) based on code inspection of `/rip` and `/athena`.
- **Actions Taken:**
  - `IMPLEMENTATION_PLAN.md`: Updated Phase 1 through Phase 5 checklists with missing files (`classutils.py`, `constants.py`, `streaming.py`, `artifacts.py`); explicitly decoupled providers, agents, and tools from `app.plugins.api` (`ProviderPlugin`/`AgentPlugin`/`ToolPlugin`); clarified `rag_query.py` reuse of the `VectorRAG` singleton (preventing PyTorch model reloading spikes); noted PostgreSQL implementation for `store/runs.py`; and added UI hooks for cancel button in `ChatArea.tsx`/`Footer.tsx`.
  - `MERGE_PLAN.md`: Updated What's Kept from Athena table, Backend Directory Structure, and Implementation Order; appended Decision Log Q30.
  - `ADR.md`: Added ADR-017 documenting direct capability inheritance, tool decoupling, and `VectorRAG` singleton reuse.
- **Status:** Completed.

---

## [2026-09-12] - Pre-implementation review hardening (Q31–Q38)

- **Task:** Lock integration gaps from merge plan review; update docs + schema before Phase 1 implementation.
- **Actions Taken:**
  - `MERGE_PLAN.md`: Run worker contract (Q31); sources SSE (Q32); drop rewritter (Q33); file-based artifacts (Q34); structural-only event persistence (Q35); deterministic aggregator (Q36); admin health stub (Q37); `conversation_summary` rename (Q38); Decision Log Q31–Q38.
  - `IMPLEMENTATION_PLAN.md`: conftest in Phase 1.1; worker rewrite in Phase 4; frontend sources/delta/artifact handling; smoke test updates.
  - `ADR.md`: ADR-018..024; updated ADR-014/015/016.
  - `CONTEXT.md`, `AGENT.md`, `SETUP.md`: terminology and traps.
  - `backend/schema.sql`: `notebooks.summary` → `conversation_summary`.
- **Status:** Completed.

---

## [2026-09-12] - PHASE 1.1 - backend/app/ root (move + import map + conftest)
- **Task:** IMPLEMENTATION_PLAN Phase 1.1 — create `backend/app/` root, move `core/rag/routes/services/` under it, copy `classutils.py`/`constants.py`, apply `app.*` import map, delete `services/rewritter.py` + `services/llm.py` (Q33), migrate `tests/conftest.py` (Q38), Dockerfile CMD → `app.main:app`.
- **Actions Taken:**
  - MOVED `backend/core/` → `backend/app/core/`, `rag/` → `app/rag/`, `routes/` → `app/routes/`, `services/` → `app/services/`, `backend/app.py` → `backend/app/main.py`, `backend/config.py` → `backend/app/core/config.py` (interim hybrid: legacy constants + minimal `Settings`; Phase 1.3 replaces wholesale).
  - COPIED `athena/backend/app/core/classutils.py`, `constants.py` → `rip/backend/app/core/`; added empty `app/__init__.py`, `app/routes/__init__.py`.
  - Import map applied (`core./rag./routes./services.→app.*`, `import config→from app.core import config`); DELETED `app/services/rewritter.py` + `llm.py`.
  - `app/main.py`: dropped `llm.router` (orphaned `app/routes/llm.py` kept on disk, not imported, pending Phase 4.3 deletion — otherwise boot breaks on deleted `services/llm` imports).
  - `app/core/config.py`: fixed `BASE_DIR` to backend dir (two levels up); added minimal `Settings` (`port=8000`, `ollama_*`, `cors_origins: str`).
  - `backend/tests/conftest.py`: `from app.main import app`, `from app.core import config` + `from app.core.config import Settings`, Ollama probe `OLLAMA_BASE_URL/api/tags`; `test_app.py` imports updated.
  - `backend/Dockerfile` CMD → `app.main:app`.
- **Verification:**
  - Flat-import grep over `backend/app` + `backend/tests` → 0 hits.
  - Light imports resolve (`Settings(port=8000)`, `db`, `routes.notebooks/messages`, `chat`, `classutils`/`constants`) — EXIT 0.
  - `ruff check backend/app` (ml_env ruff 0.16.6): 26 E/F hits, all pre-existing style (E501/F541/F401/F841/E722) — no new import errors (no F821).
  - `pytest --collect-only`: ERROR — `test_app.py` module-level `from app.main import app` pulls `sentence_transformers` → broken `torchvision::nms` in `ml_env` (environmental, pre-dates move; prior `agent_env` no longer exists). Full green needs working torch + Postgres + Ollama.
- **Status:** Completed with noted deviations (llm.router exclusion, interim hybrid config, pytest blocked by env).
- **Commits (multi-stage, AGENT.md §4b):** `485ee75` refactor(backend) app move + import map; `2bec1c5` test(backend) conftest migration; `3724e24` chore(docker) CMD; docs commit follows (this file + checkbox + §4b workflow).

---

## [2026-09-12] - PHASE 1.2 - verify schema.sql (no drift, read-only)
- **Task:** IMPLEMENTATION_PLAN Phase 1.2 — verify `backend/schema.sql` has `conversation_summary` + `summary_message_count`, `messages` by `notebook_id` only, `runs` + `run_events`; prove re-runnable.
- **Actions Taken:**
  - Static grep: 6x `CREATE TABLE IF NOT EXISTS` (notebooks/files/embeddings/messages/runs/run_events); `conversation_summary` + `summary_message_count` on notebooks (Q38 rename already in place); messages columns `message_id/notebook_id/role/text/sources/created_at` — no `conversation_id`; no `conversations` table, no `LLM_URL`/`NEO4J`; all indexes `IF NOT EXISTS`, embeddings FK via `DO` guard → re-runnable by construction. No drift → file untouched (read-only).
  - Live full apply: BLOCKED — no `psql` binary, Docker daemon down, local Postgres 17.4 has no pgvector (`CREATE EXTENSION vector` fails; scratch db created/dropped clean, 0 tables).
  - Live stripped apply (vector ext/col/index removed, scratch `rip_phase12_partial`): APPLY-1 ok, APPLY-2 ok, 6 tables listed, notebooks/messages columns as required; scratch db dropped.
- **Verification:** static full pass; live stripped 2x ok; full pgvector apply deferred to compose postgres (`pgvector/pgvector:pg16`, Phase 1.5).
- **Status:** Completed with env caveat (full live apply needs compose postgres).
- **Commits (multi-stage, AGENT.md §4b):** docs-only stage follows (this file + checkbox).

---

## [2026-09-12] - PHASE 1.3 - rewrite app/core/config.py (TARGET Settings)
- **Task:** IMPLEMENTATION_PLAN Phase 1.3 — REPLACE `app/core/config.py` with MERGE_PLAN §Config TARGET (port 8000, OLLAMA_*, no LLM_URL/NEO4J_*, `cors_origins: str`, BGE alias support).
- **Actions Taken:**
  - Wrote TARGET block verbatim (all fields incl. Q28 memory comments, sandbox, langfuse, `api_keys_json: dict`).
  - 3 forced deltas (pre-1.4 env): `AliasChoices` for `bge_m3_model_path`/`bge_reranker_v2_m3` (Q5 — current `.env` only sets `BGE_MODEL_DIR`/`RERANKER_MODEL_DIR`); `extra="ignore"` (current `.env` still has forbidden `LLM_URL`; settings default forbid would crash boot until 1.4 rewrites env); `settings = Settings()` singleton for call sites.
  - Migrated all legacy module-constant users to `settings.*`: `rag/pipeline.py` (bge paths), `services/file_processor.py` + `routes/files.py` (upload_dir), `tests/conftest.py` + `tests/test_app.py` (upload_dir). Zero `config.UPLOAD_DIR|BGE_*|LLM_URL` refs remain.
- **Verification:**
  - Plan test: `Settings().port` → `8000` ✓.
  - Proof script: boots with legacy keys absent (no `llm_url`/`neo4j*`/`database_url` fields); `BGE_MODEL_DIR`/`RERANKER_MODEL_DIR` aliases honored; primary names honored; `cors_origins` is `str="*"`; singleton ok.
  - Light imports resolve; `ruff --select E,F`: 7 new E501s, all TARGET-verbatim comment lines (repo baseline already 19x E501 — style debt, not logic).
- **Status:** Completed with noted deltas (aliases/extra-ignore/singleton; extra-ignore drops when 1.4 cleans `.env`).
- **Commits (multi-stage, AGENT.md §4b):** `refactor` (app config + usages) → `test` (conftest/test_app) → `docs` (this file + checkbox).

---

## [2026-09-12] - PHASE 1.4 - pyproject.toml + .env.example (TARGET deps/env)
- **Task:** IMPLEMENTATION_PLAN Phase 1.4 — REPLACE `pyproject.toml [project]` + `.env.example` with MERGE_PLAN TARGET blocks (Q29: keep langfuse, drop google-genai/neo4j; CORS_ORIGINS=*, BGE aliases; no requirements.txt/neo4j/DATABASE_URL).
- **Actions Taken:**
  - `pyproject.toml [project]` replaced wholesale: `rip 1.0.0`, full TARGET dep list (uvicorn[standard], langgraph, langfuse, pyyaml, python-docx, reportlab added; Dropped google-genai/neo4j comments kept verbatim). `[build-system]` + `[tool.ruff]`/`[tool.pytest]` sections kept — tooling, not deps.
  - `.env.example` written TARGET-verbatim (DB rip/rip, Ollama localhost:11434/qwen2.5:14b, PORT 8000, CORS_ORIGINS=*, BGE primaries + commented aliases, sandbox, langfuse-optional).
  - Box test `copy .env.example .env` executed; live `.env` (gitignored, local-only) BGE primaries → `D:/models/...` machine-local override (TARGET `./backend/models/...` placeholders don't exist on this box). DB keys kept as TARGET (compose alignment is Phase 1.5).
  - Installed missing TARGET deps into ml_env: `python-docx 1.2.0`, `reportlab 5.0.1`, `pgvector 0.5.0`, `docling 2.126.0` (all ≥ pins; full set of 15 verified via pip list).
- **Verification:**
  - Forbidden grep: no live `neo4j`/`DATABASE_URL`/`google-genai`/`LLM_URL` refs (only TARGET's own Dropped comments); no `requirements*.txt` anywhere.
  - All non-torch TARGET deps import ok (fastapi … reportlab + docling 2.126.0).
  - Settings boots from new `.env`: port 8000, ollama localhost:11434/qwen2.5:14b, bge `D:/models/bge-m3`, cors `'*'`, db localhost/5432/rip/rip.
- **Deviations / blockers:**
  - Box test `pip install -e .` NOT APPLICABLE: setuptools finds `backend/`+`frontend/` as flat-layout packages and refuses discovery; repo has no root-level importable package (backend runs from `backend/` workdir by design). Deps installed directly instead — same environment outcome. Do NOT "fix" by restructuring in this phase.
  - `GET /api/health` boot still blocked (env, pre-existing): torch-stack rot now surfaces at `torchaudio/libtorchaudio.pyd` load (`torchaudio 2.5.1+cu121` vs `torch 2.14.0`); docling pulled torchvision 0.20.1→0.29.0 as a side effect. Import chain `app.main→routes.files→dependencies→vector_rag→pipeline→sentence_transformers→transformers→torchaudio` fails on the native lib, not repo code. Fix = torch reinstall / fresh venv in Phase 6 (install from pyproject pins, not `-e`).
- **Status:** Completed with noted deviations (local .env BGE override; -e install N/A; health boot deferred to Phase 6 env repair).
- **Commits (multi-stage, AGENT.md §4b):** chore (pyproject + .env.example) → docs (this file + checkbox).

---

## [2026-09-12] - PHASE 1.5 - replace docker-compose.yml (TARGET, postgres healthy)
- **Task:** IMPLEMENTATION_PLAN Phase 1.5 — REPLACE `docker-compose.yml` with MERGE_PLAN TARGET (Q29 breaking: db→postgres, prototype_rip/trainee→rip/rip, postgres_data→pgdata); `down` → `config` → `up postgres` healthy.
- **Actions Taken:**
  - Wrote TARGET block verbatim (migration-warning header kept; backend 8000:8000 + DB_HOST=postgres + OLLAMA host.docker.internal + env_file; frontend 5173:80; postgres pgvector/pgvector:pg16 + pgdata + schema.sql init mount + pg_isready healthcheck; redis commented optional).
  - `docker compose down` exit 0 (nothing running; old file couldn't even interpolate — required LLM_URL already gone from .env by design).
  - `docker compose config` valid; interpolated backend env confirms wiring (DB_HOST=postgres override, DB rip/rip, OLLAMA host.docker.internal, PORT 8000).
- **Verification (same-image harness — see blocker):**
  - `pgvector/pgvector:pg16` pulled + cached; harness container on host 5433 with identical env + schema.sql init mount: init logs show CREATE TABLEs, DB ready.
  - Live proof: 6 tables (notebooks/files/embeddings/messages/runs/run_events), `vector` ext present, notebooks has conversation_summary+summary_message_count, messages notebook_id-only.
  - Re-apply `schema.sql` with ON_ERROR_STOP=1 → exit 0 (NOTICEs only). This also retro-completes the Phase 1.2 full-pgvector live proof.
  - Harness stopped + removed (`rm -v`); image kept for `up`.
- **Blocker (environmental, one step left):** host 5432 is held by local `postgresql-x64-17` service (PID 8228); `Stop-Service` needs admin (access denied), so the literal `docker compose up postgres` bind can't complete in-session. Compose file itself is TARGET-final — only host-port availability is missing. To finish: stop the local postgres service (admin) or disable it, then `docker compose up -d postgres` (no pull needed). Note: pre-existing orphan volumes (`athena_pgdata`, `backend_pgdata`) left alone; `athena-frontend-1` currently holds host 5173 (matters at Phase 5, not here).
- **Status:** Completed except literal `up` bind (proven equivalent via harness).
- **Follow-up [2026-09-12] — user-directed host-port override:** host 5432 stays taken (no admin to stop local postgres), so per user instruction: compose `ports` → `"${HOST_PG_PORT:-5432}:5432"` (TARGET default preserved; container port untouched, backend still uses `postgres:5432`), live `.env` (gitignored) gains `HOST_PG_PORT=5433`. `docker compose up -d postgres` → `rip-postgres-1` **healthy in 5s** on host 5433, fresh `rip_pgdata` init applied schema.sql: 6 tables + `vector` ext confirmed live. Phase 1.5 Done fully met.
- **Commits (multi-stage, AGENT.md §4b):** chore (compose file) → docs (this file + checkbox).
- **Follow-up commits:** `f173059` chore (HOST_PG_PORT) + `9663d4a` docs.

---

## [2026-09-12] - PHASE 2.1 - providers (base/ollama/streaming, minicpm5 round-trip)
- **Task:** IMPLEMENTATION_PLAN Phase 2.1 — COPY Athena `providers/base.py`, `ollama.py`, `streaming.py` → `rip/backend/app/providers/`; drop plugin system (ModelProvider direct); gemini_model_* → ollama_default_model; DO NOT copy gemini/llama_server/tracing; pytest test_providers.py; Ollama chat + ThinkFilter incremental strip.
- **Actions Taken:**
  - `base.py`: verbatim copy (no plugin imports, no gemini identifiers — only prose).
  - `ollama.py`: dropped `app.plugins.api` import; `OllamaProvider(ModelProvider)`; removed `plugin_id`/`version`/`init()`/`health()` (plugin lifecycle); gemini prose neutralized (docstring multi-model note, served_model comment); `generate_structured` schema name `athena`→`rip`; `get_logger`→stdlib `logging.getLogger` (RIP logging.py has only setup_logging).
  - `streaming.py`: logic verbatim; `get_logger`→stdlib; docstring `(ollama, llama-server)`→`(ollama)` (llama-server not copied per box).
  - New `backend/tests/test_providers.py`: 7 ThinkFilter/strip_think unit tests + 5 live tests (provider isinstance, model resolution foreign→default/local→verbatim, listing, generate + stream round-trip) vs `openbmb/minicpm5-2b:latest` (override `RIP_TEST_MODEL`).
- **Verification:** `pytest backend/tests/test_providers.py -q --noconftest` → **12 passed** (16s); live generate+stream both returned `hello rip`, no `<think>` leak, usage recorded; ruff E/F: 6x E501, all Athena-verbatim lines (inherited style debt, per 1.1/1.3 precedent).
- **Deviations / notes:**
  - Per user instruction: testing on `openbmb/minicpm5-2b:latest`, NOT qwen2.5:14b — started pull job killed before completion. Box Done `qwen2.5:14b chat succeeds` therefore NOT proven; default-model path only proven via foreign→default *resolution* (serves qwen name, never executed). Live qwen proof needs the 9GB pull — defer to Phase 6 smoke (or pull when needed).
  - `--noconftest` required: shared conftest imports app.main → torch-stack rot (standing blocker); full-suite green awaits Phase 6. Also `PYTHONPATH=backend` + `OLLAMA_BASE_URL=localhost` needed in this shell (TARGET config default is host.docker.internal for compose; bare Settings from backend/ CWD misses rip/.env — main.py's load_dotenv covers real runs).
  - Minicpm5 quirk found (model, not provider): ≤32-token budget can end inside `<think>` → honest empty string after strip; 256 budget returns clean text.
- **Status:** Completed with noted deviation (qwen live proof deferred).
- **Commits (multi-stage, AGENT.md §4b):** feat (providers) → test (test_providers.py) → docs (this file + checkbox).
