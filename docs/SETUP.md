# Setup Guide: Local Development

This guide reflects the repository as it exists today. Every file and variable named below is verified against the source — do not rely on older instructions that mention `requirements.txt` or `DATABASE_URL`.

---

## 1. Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.11+ (per `pyproject.toml`) |
| Node.js | 20.19+ or 22.12+ (Vite 7 requirement; no `engines` pinned in `package.json`) |
| PostgreSQL | Server reachable over the network; **pgvector extension must be enabled** (the schema uses `vector(1024)`, and no repo code or script creates the extension) |
| Neo4j | Running and reachable at the `NEO4J_URI` configured below |
| Local model weights | Present on disk at the hardcoded paths in `backend/config.py` (see below) |
| LLM endpoint | An OpenAI-compatible HTTP server (`/v1/chat/completions`) serving `qwen2.5-coder-14b`, reachable at the `LLM_URL` configured below |

### Local model paths (hardcoded in `backend/config.py`)
These are **Windows absolute paths baked into code**, not environment-driven:

| Constant | Path |
|---|---|
| `BGE_M3_MODEL_PATH` | `D:\models\bge-m3` |
| `BGE_RERANKER_V2_M3` | `D:\models\reranker\bge_reranker_v2_m3` |

`backend/download.py` is a one-off utility that downloads `BAAI/bge-m3` to `D:/models/bge-m3` (requires HF Hub access at download time only). It does not download the reranker.

---

## 2. Environment configuration

The backend reads `.env` via `python-dotenv` (`backend/app.py`, `backend/core/db.py`). Run the server from `backend/` and the repo-root `.env` is discovered. Start from the template:

```powershell
copy .env.example .env
```

Keys used by the code (`.env.example` + `backend/config.py` + `backend/core/db.py`):

| Key | Used by | Notes |
|---|---|---|
| `LLM_URL` | `backend/config.py` → all LLM calls (`services/llm.py`, `rag/pipeline.py`, `services/rewritter.py`) | Base URL of the OpenAI-compatible endpoint, e.g. `http://10.10.30.65:8000`. **This is the one that matters.** |
| `DB_NAME` | `core/db.py` | Postgres database name (template default: `prototype_rip`) |
| `DB_USER` | `core/db.py` | Postgres user |
| `DB_PASSWORD` | `core/db.py` | Postgres password |
| `DB_HOST` | `core/db.py` | Postgres host |
| `DB_PORT` | `core/db.py` | Default `5432` |
| `NEO4J_URI` | `core/db.py` | Default `bolt://localhost:7687` |
| `NEO4J_USER` | `core/db.py` | Default `neo4j` |
| `NEO4J_PASSWORD` | `core/db.py` | — |

> Note: `backend/app.py` reads `LLM_API_URL`, but no request path uses it — the services all read `LLM_URL`. Set `LLM_URL`. See Known Issues.

---

## 3. Database bootstrap

No migration/bootstrap script exists. Create the schema once, manually:

1. Create the database named in `DB_NAME` (or point `DB_NAME` at an existing one).
2. Ensure the pgvector extension is available in that database:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
3. Apply the schema:
   ```powershell
   psql "host=<DB_HOST> port=<DB_PORT> dbname=<DB_NAME> user=<DB_USER>" -f backend\schema.sql
   ```

`schema.sql` creates four tables — `notebooks`, `files`, `messages`, `embeddings_test` — plus an HNSW vector index (`embeddings_test_embedding_idx`) and a GIN full-text index (`text_search_idx`). Neo4j needs no schema bootstrap; nodes/relationships are created by the ingestion pipeline.

---

## 4. Backend install & run

There is **no `requirements.txt`**. The single dependency manifest is `pyproject.toml` at the repo root.

```powershell
# from the repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# install the declared dependencies (the project itself is the runnable app)
pip install -e .
```

Then run the FastAPI server **from the `backend/` directory** (imports are top-level: `app`, `config`, `core`, `rag`, `routes`, `services`):

```powershell
cd backend
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Health check: `GET http://localhost:8000/api/health` → `{"status": "ok"}`.

> On startup the lifespan loads the embedding model and reranker from the `D:\models\...` paths and opens a Neo4j driver. If those are unavailable, startup fails.
>
> The frontend expects this backend on the port in `frontend/src/config.js` (currently `http://localhost:8000`). See the port mismatch note under Known Issues in `docs/ARCHITECTURE.md`.

---

## 5. Frontend install & run

```powershell
cd frontend
npm install
npm run dev
```

- The Vite dev server binds `0.0.0.0` (`frontend/vite.config.js`); visit the printed local URL (default `http://localhost:5173`).
- The backend base URL is hardcoded in `frontend/src/config.js` (`export const API = "http://localhost:8000"`). There is **no frontend `.env`**.
- Lint: `npm run lint`. Build: `npm run build`.

---

## 6. Smoke test

1. Open the frontend, create a notebook.
2. Upload a PDF via the left "Knowledge Base" sidebar. The file row shows `processing`, then `ready`.
3. Ask a question in the chat footer and confirm a streamed answer with sources appears.

---

## 7. Tests & lint

| Tool | Command | Location |
|---|---|---|
| Backend tests | `pytest` | `pyproject.toml` (`testpaths = ["backend/tests"]`) |
| Backend lint | `ruff` | Configured in `pyproject.toml` |
| Frontend lint | `npm run lint` | `frontend/` |

Caveat: the single backend test (`backend/tests/test_app.py`) constructs the FastAPI app, so it loads the local models and opens Neo4j on startup; it only covers the health endpoint.

---

## Troubleshooting

- **`Error connecting to database`** → check `DB_*` vars and that pgvector exists (see step 3).
- **LLM calls fail / answers are empty** → confirm the host serving `qwen2.5-coder-14b` is reachable at `LLM_URL` and exposes `/v1/chat/completions`.
- **Uploads get stuck on `processing`** → the frontend triggers `/process` on a hardcoded `localhost:5000` (`frontend/src/hooks/notebooks/useFiles.js`), not the port in `config.js`. See Known Issues.
- **Startup crash about models** → the `D:\models\...` weights are missing; correct the paths in `backend/config.py` for your machine.
