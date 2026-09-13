# RIP Caveats — read before debugging

Environment traps and behavioral gotchas verified against the live system. Each entry: symptom → cause → fix.

---

## Environment

### `localhost` Postgres hangs on Windows — use `127.0.0.1`

- **Symptom:** every `pg_connection` takes ~21s; runs crawl.
- **Cause:** `localhost` resolves to IPv6 first and blackholes; the driver falls back to IPv4 only after timeout.
- **Fix:** host-local runs/tests use `DB_HOST=127.0.0.1` + `HOST_PG_PORT` (e.g. `5433` when host 5432 is taken). In-compose backend keeps `DB_HOST=postgres:5432`.

### `OLLAMA_BASE_URL` differs host vs compose

- **Symptom:** provider tests self-skip as "Ollama down", or the container can't reach Ollama.
- **Cause:** compose default `http://host.docker.internal:11434` is unresolvable from the host.
- **Fix:** export `OLLAMA_BASE_URL=http://localhost:11434` for host-local runs/tests.

### BGE model paths must be absolute

- **Symptom:** backend boot crash loading embeddings.
- **Cause:** relative `./backend/*` paths assume CWD=`rip/`, but uvicorn runs from `rip/backend/` (compose uses `/app/backend`).
- **Fix:** absolute paths in `.env` (`BGE_M3_MODEL_PATH`, `BGE_RERANKER_V2_M3`); `BGE_MODEL_DIR`/`RERANKER_MODEL_DIR` are honored aliases.

### Never install `torchaudio`

- **Symptom:** `import transformers` crashes loading a CUDA `.pyd`.
- **Cause:** orphan `torchaudio` (nothing requires it); its presence trips an audio-utils guard, then the mismatched binary load fails.
- **Fix:** keep it uninstalled. If reinstalled, its CUDA build must version-match the CPU torch.

### Torch/CUDA teardown dump is not a failure

- **Symptom:** access-violation noise after a passing test run.
- **Cause:** torch/CUDA teardown on Windows; process still exits 0.
- **Fix:** ignore when exit code is 0 and all tests report pass.

### In-container PDF ingest needs Java

- **Symptom:** PDF uploads stall in `processing` inside compose while host-local works.
- **Cause:** the Docling path needs a Java runtime absent from the image.
- **Fix:** install a JRE in `backend/Dockerfile` or pre-process PDFs host-side.

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
- **`notebook_id` is injected, never generated.** The planner must never emit it; the engine overwrites tool inputs from the run.
