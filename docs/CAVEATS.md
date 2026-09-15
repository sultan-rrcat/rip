# RIP Caveats — read before debugging

Environment traps and behavioral gotchas verified against the live system. Each entry: symptom → cause → fix.

---

## Environment

### `localhost` Postgres hangs on Windows — use `127.0.0.1`

- **Symptom:** every `pg_connection` takes ~21s; runs crawl.
- **Cause:** `localhost` resolves to IPv6 first and blackholes; the driver falls back to IPv4 only after timeout.
- **Fix:** host-local runs/tests use `DB_HOST=127.0.0.1` + `HOST_PG_PORT` (e.g. `5433` when host 5432 is taken). In-compose backend keeps `DB_HOST=postgres:5432`. `localhost` is auto-normalized to `127.0.0.1` and connections fail fast via `DB_CONNECT_TIMEOUT_S=5`.

### `OLLAMA_BASE_URL` differs host vs compose

- **Symptom:** provider tests self-skip as "Ollama down", or the container can't reach Ollama.
- **Cause:** compose default `http://host.docker.internal:11434` is unresolvable from the host and from Linux Engine without a gateway mapping.
- **Fix:** export `OLLAMA_BASE_URL=http://localhost:11434` for host-local runs/tests. Compose sets `extra_hosts: ["host.docker.internal:host-gateway"]` so the mapping works on Windows and Linux. Init probes 5s and degrades (per-request fail-honest), never boot-crashes.

### BGE model paths must be absolute

- **Symptom:** backend boot crash loading embeddings.
- **Cause:** relative `./backend/*` paths assume CWD=`rip/`, but uvicorn runs from `rip/backend/` (compose uses `/app/backend`).
- **Fix:** absolute paths in `.env` (`BGE_M3_MODEL_PATH`, `BGE_RERANKER_V2_M3`); `BGE_MODEL_DIR`/`RERANKER_MODEL_DIR` are honored aliases. Relative values auto-resolve to repo-root absolute and fail fast with the missing path if weights are absent.

### Never install `torchaudio`

- **Symptom:** `import transformers` crashes loading a CUDA `.pyd`.
- **Cause:** orphan `torchaudio` (nothing requires it); its presence trips an audio-utils guard, then the mismatched binary load fails.
- **Fix:** keep it uninstalled. If reinstalled, its CUDA build must version-match the CPU torch.

### Torch/CUDA teardown dump is not a failure

- **Symptom:** access-violation noise after a passing test run.
- **Cause:** torch/CUDA teardown on Windows; process still exits 0.
- **Fix:** ignore when exit code is 0 and all tests report pass.

### In-container PDF ingest needed Java (resolved in image)

- **Symptom (pre-fix images):** PDF uploads stall in `processing` inside compose while host-local works.
- **Cause:** the Docling path needs a Java runtime, absent from older images.
- **Fix:** the image now installs `default-jre-headless` (`backend/Dockerfile`, `JAVA_HOME` set, `java -version` verified at build). No action needed on current builds — if uploads still stall, check BGE weights and Ollama reachability first.

### Compose Postgres init runs once

- **Symptom:** `backend/schema.sql` changes have no effect after `docker compose up`.
- **Cause:** the `./backend/schema.sql:/docker-entrypoint-initdb.d/001-schema.sql:ro` mount runs only on an empty `pgdata` volume.
- **Fix:** re-apply via `psql "host=127.0.0.1 port=<HOST_PG_PORT> ..."` or `docker compose down -v` for a fresh bootstrap (deletes data). For additive column changes the backend also self-heals: lifespan runs idempotent `ADD COLUMN IF NOT EXISTS` migrations (fail-soft), and the messages routes serve the legacy shape when the column is absent — so chat survives even before the DDL lands.

### Stale shell `DB_*` exports shadow `.env` for Postgres init

- **Symptom:** `FATAL: database "trainee" does not exist` every 5s, or the backend can't connect though `.env` says `rip/rip/rippass`.
- **Cause:** `POSTGRES_*` in `docker-compose.yml` interpolate from the *shell* (`${DB_USER:-rip}`), where prototype-era `DB_*` exports win over the project `.env`. The backend instead reads `.env` via `env_file`, so the two sides disagree. The healthcheck now probes `$POSTGRES_USER`/`$POSTGRES_DB` (immune), but init still needs a clean shell.
- **Fix:** in the terminal you run compose from, drop the leftovers (session-only): `Remove-Item Env:DB_HOST, Env:DB_NAME, Env:DB_USER, Env:DB_PASSWORD, Env:DB_PORT`. Then `docker compose down -v` + `up` for a fresh init. Durable fix: remove them from `$PROFILE`/activate scripts.

### Host port already allocated (demo box runs many stacks)

- **Symptom:** `Bind for 0.0.0.0:8000 failed: port is already allocated` (firewatch holds 8000; athena holds 5173).
- **Fix:** container ports stay fixed; remap the host side only: `$env:HOST_BACKEND_PORT=8005; $env:HOST_FRONTEND_PORT=5174; docker compose up -d`.

### Container healthchecks (what probes what)

| Service | Probe | Timing |
|---|---|---|
| `postgres` | `pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"` (container env, immune to shell `DB_*` shadow) | `interval 5s`, `retries 10`, no `start_period` |
| `backend` | stdlib `urllib` `GET http://127.0.0.1:8000/api/health` (no curl in slim image) | `interval 10s`, `retries 8`, `start_period 180s` — lifespan blocks serving until BGE weights load, so the grace is generous |
| `frontend` | `wget --spider http://127.0.0.1/healthz` | `interval 15s`, `retries 3`, `start_period 30s` |

Gating chains `postgres → backend → frontend`. Probes use `127.0.0.1`, never `localhost`: BusyBox `wget` resolves `::1` first and nginx would refuse (same IPv6-first trap as Postgres on Windows). The backend probe targets the fixed container port `8000` — compose pins `PORT=8000`, so they agree by construction.

### `OLLAMA_BASE_URL` interpolates, `LANGFUSE_HOST` is pinned

- `OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}` — your `.env` value flows into the container; the gateway is only the fallback. Host Ollama vs LAN remote is a `.env` edit, not a compose edit.
- `LANGFUSE_HOST: http://host.docker.internal:3002` is hard-pinned — the `.env` value (`http://localhost:3002`, correct for host-local runs) is ignored in compose. Asymmetry is intentional (Langfuse UI address is host-published; see SETUP §4) but easy to misread.

### Frontend is same-origin only

- **Symptom:** `docker run -e VITE_API_URL=... frontend` still calls the old backend.
- **Cause:** `VITE_API_URL` is baked at `npm run build`; the image sets it empty so `/api/` + `/v1/` go through nginx to `backend:8000`.
- **Fix:** retarget by rebuilding with a different `VITE_API_URL`; no runtime override (intentional, Q4-A).

---

## Langfuse

### Localhost needs proxy bypass + `127.0.0.1`

- **Symptom:** Langfuse UI/API unreachable from shell (`Invoke-WebRequest` timeout) though `docker ps` shows the web container up.
- **Cause:** proxy env vars intercept loopback requests.
- **Fix:** `NO_PROXY`/`no_proxy` including `localhost,127.0.0.1` (or empty proxy vars) and address the UI as `http://127.0.0.1:3002`.

### Use the v4 observations API

- **Symptom:** `traces list` errors with "Cannot call deprecated API operation" on server ≥ 4.x.
- **Cause:** `GET /api/public/traces` is removed in Langfuse v4.
- **Fix:** `observations list --fields core,basic,time,io,metadata,model,usage,trace_context [--trace-id …]`.

### Tracing is opt-in and restart-gated

- Enabling requires `LANGFUSE_ENABLED=true` + keys **and a backend restart** (`init_langfuse` runs at boot). Disabled path is behavior-identical.

### Outer spans are fragile (no parent-context re-entry)

- The engine opens `plan`/`aggregate` spans directly in LangGraph node bodies without re-entering the worker thread's context (Athena had `_with_parent_ctx`; RIP dropped it — the "spans removed" docstring in `engine.py` is stale, the spans exist).
- If LangGraph ever schedules outer nodes on pool threads, `plan`/`aggregate` detach into orphan traces. Inner `step:{id}` spans are safe (per-node context copies). If plan spans go missing, this is the first suspect.

---

## Protocol (frontend/backend boundary)

- **`messages` are frontend-owned.** Backend `runs/manager` never writes them. Dedup SSE by `seq`.
- **`conversation_summary` ≠ SSE `summary`.** DB column = internal memory compression; SSE event = final answer text.
- **`delta` is live-only.** Never persisted; reconnect rebuilds text from `step_completed`/`summary`.
- **No query rewrite.** The Planner carries retrieval intent; there is no rewrite layer.
- **Artifacts are disk URLs**, never inline base64 (`{upload_dir}/{notebook}/{artifacts}/{run}/{step}/{file}` + `index.json`).
- **Bare JSON on `/v1/*`.** No BFF envelope there; `CORS *`; port `8000`.

## Planner behavior

- **Validator is strict `exactly-one-of`.** Every step needs precisely one of `agent_id`/`tool_id` from the known sets; `depends_on` must reference exact `step_id`s (no `step_` prefixes); max 10 steps. Violations fail the run honestly before execution.
- **Small planner models underperform.** A 2B planner was observed emitting executor-less steps (validation failure) and empty plans. Default to `qwen2.5:14b`; if a smaller override is used, expect planning-quality loss. Trivial requests are covered by the ADR-026 fallback (single `reasoning` step).
- **`notebook_id` is injected, never generated.** The planner must never emit it; the engine overwrites tool inputs from the run. Same holds for `notebook.inspect` and `doc.convert` (`file_id` comes only from the `Notebook documents:` snapshot or `"*"` for convert-all; never invented, never a `{{step}}` placeholder).
- **Placeholder hygiene.** Placeholders only carry whole step output text (`{{1}}`). Dotted forms like `{{1.files[0].id}}` do not exist and must never be emitted; structured data from `notebook.inspect` cannot be chained to `doc.convert`. `notebook.inspect` is a freshness probe only (single step, never feed another step).
- **Report vs convert routing.** Factual/QA with ready docs → `rag.query` first (never parametric-only). Plural convert-all → single `doc.convert` with `file_id:"*"`. Single named convert → single `doc.convert` with literal `file_id` from snapshot. Ambiguous convert or missing format → single-`reasoning` counter-question. Report-format → `doc.generate`. Never `doc.convert` for reports.
