# RIP — Critical Issues & Urgent Improvements Plan

> Status: drilled 2026-09-30, all Round-1 recommendations accepted.
> Source: full `docs/` read (ARCHITECTURE, SETUP, ADR-001–032, AGENT, CONTEXT) + codebase sweep (backend `app/`, `schema.sql`, frontend `src/`, compose, scripts, tests).

## Decisions (locked)

- **F1:** capture-before-detach + refuse-empty guard; leave existing empty rows (no backfill).
- **F2:** unblock + error bubble, no auto-retry.
- **B1:** 15s heartbeat + additive `?last_seq=` resume, bounded queue 1000.
- **B2:** max 4 active runs, 429 + `Retry-After`, fixed 10-min run timeout.
- **B3:** lifespan reaper marks stale `processing` (>30 min) → `error`; manual re-PROCESS, no auto-requeue.
- **S1:** login+ownership — `notebooks.owner_id UUID NOT NULL FK`, scope files/messages/runs/artifacts via notebook join, 404-on-foreign, backfill single `local` owner.
- **S2:** explicit `CORS_ORIGINS` (no `*` with credentials), `Secure` in prod, login 5/min/IP, fix EventSource/cancel to `credentials:include`.
- **P1 order after P0:** (1) file_status validation + delete cleanup, (2) migrations, (3) dotenv drift. CI deferred with follow-up ticket.

---

## P0 — Critical (data-loss / hang / unauthorized access)

### F1. Assistant messages persist as empty — `frontend/src/hooks/notebooks/useMessages.ts:73-132`
- Cause: `finalizeCompleted` calls `detach()` (`58-71` clears `textRef/sourcesRef/artifactsRef`) before `createMessageAPI(..., textRef.current, ...)` (`109-115`).
- Fix: capture `text/sources/artifacts` locals before `detach()`; add refuse-empty guard (keep on-screen + log, never persist `''`).
- Verify: manual send → reload → assistant row non-empty; unit test capture-before-clear.

### F2. Send permanently blocked after first failure — `useMessages.ts:473-544`
- Cause: `isRunningRef.current=true` (`476`) only cleared in `detach()`; early throw before `attach()` leaves it stuck, `isRunning` state stays `false`.
- Fix: `try/finally` + reset in `catch`; show error bubble, let user resend (no auto-retry to avoid duplicate user rows).
- Verify: fail `createRun` once (offline backend) → second send works.

### B1. SSE stream never disconnects — `backend/app/api/runs.py:108-126`, `backend/app/runs/manager.py:98-118`
- Cause: `while True: live.get()` blocking, no `is_disconnected`, heartbeat, timeout, or `Last-Event-ID`; unbounded `queue.Queue()`.
- Fix: disconnect poll + 15s heartbeat comment, bounded queue (1000, drop-oldest or 503), additive `?last_seq=` resume (old clients ignore).
- Verify: curl disconnect drops thread; reconnect with `last_seq` resumes without dupes.

### B2. Unbounded thread-per-run + tiny DB pool — `manager.py:168-174 MAX_RUNS=500`, `core/db.py:41 minconn=2,maxconn=10`
- Cause: `threading.Thread(daemon=True)` per run, no semaphore; `orchestrator.run` no timeout vs `ollama_timeout_ms=120s`.
- Fix: semaphore/max 4 active runs (matches single Ollama + 2-parallel-writes rule, CAVEATS), 429 + `Retry-After` on overflow, fixed 10-min run timeout, revisit pool sizing (e.g. 4/20).
- Verify: 5 concurrent runs → 5th gets 429; pool stats healthy.

### B3. Uploads strand in `processing` — `routes/files.py:229`, `services/file_processor.py:12-83`, ADR-005
- Cause: `BackgroundTasks.add_task(run_rag_pipeline)` fire-and-forget, no retry/timeout/cancel; restart orphans file; no `updated_at`.
- Fix: lifespan reaper marks `processing` older than 30 min → `error` on boot; `POST /process` stays manual retry (no auto-requeue).
- Verify: kill mid-ingest → reboot → file shows `error`, re-PROCESS succeeds.

### S1. No object-level authZ — `schema.sql:9-33,84-139`, `routes/notebooks.py`, `routes/files.py`, `routes/messages.py`, `api/runs.py`
- Cause: no `owner_id` on notebooks; files/messages/runs/artifacts never check ownership.
- Fix: `notebooks.owner_id UUID NOT NULL` (+ migration backfilling `local` owner), scope all child queries via notebook join, 404-on-foreign (no 403 leak). Runs/artifacts inherit via notebook.
- Verify: user A cannot GET user B notebook/file/run (404); artifacts download scoped.

### S2. Cookie + CORS + CSRF — `routes/auth.py:106-112`, `main.py:163-168`, `frontend/src/services/runs.ts:31,67-75`
- Cause: `set_cookie(httponly,samesite=lax)` no `secure`; no login rate-limit; `allow_methods/headers=["*"]` with cookie-auth; EventSource/cancelRun omit credentials.
- Fix: explicit `CORS_ORIGINS` (never `*` with credentials), `Secure` in prod, login 5/min/IP + lockout, fix `EventSource(withCredentials)` + `cancelRun credentials:include`, match cookie path/samesite on logout.
- Verify: cross-origin stream 200 with credentials; login brute-force throttled.

---

## P1 — Urgent (next after P0, in agreed order)

1. **File status validation + delete cleanup** — `routes/files.py:106-143`: whitelist `status` (`uploading/processing/ready/error`), unlink `{upload_dir}/{notebook_id}/{file_id}{ext}` + artifacts on delete (FK cascade only clears DB today).
2. **Migrations** — compose init runs once (`schema.sql:105-116`); only `ensure_artifacts_column()` self-heals. Add versioned migrator or extend `scripts/rip.ps1 migrate`.
3. **Dotenv drift** — `.env` vs `.env.example` vs `core/config.py`: PORT 8005 vs pinned 8000, DB 5436 vs 5432, OLLAMA remote vs localhost, missing `OLLAMA_CONTEXT_WINDOW/IMAGE_MODEL/MAX_UPLOAD_SIZE/...`; `extra=ignore` masks typos — document canonical keys + fail-fast validation.
4. **Frontend API base split** — `config.ts` vs hardcoded `/api/auth/*` (`App.tsx:12`, `Login.tsx:22`, `LogoutButton.tsx:11`); stray LAN `frontend/.env` (`http://10.31.2.94:8000`) baked into builds vs same-origin prod assumption.
5. **SSE resume/dedupe** — `runs.ts:29-56` no `last_seq`; `useMessages.ts:180-198` in-memory `seenRef` reset on attach; integer `seq` collision across attempts.
6. **CSP + headers** — `nginx.conf:9 default-src 'self'` breaks MUI/Emotion + artifacts; add `style-src unsafe-inline`, `img-src data: blob:`, `connect-src`, `Referrer-Policy/HSTS/Permissions-Policy`.
7. **`http.ts` errors** — discards `detail`, crashes on 204, no timeout / `401→/login` interceptor.
8. **Upload UX** — `useFiles.ts:58-93` no size/type check, unbounded `Promise.all`, no progress/abort/retry; poll misses `uploading`.
9. **Artifact URL trust** — `artifact.ts:24-26` no scheme/host validation; SVG regex truncates answer tail.
10. **Sandbox hardening** — `tools/code_sandbox.py:47-58` add `--user nobody --cap-drop ALL --read-only --cpus`, kill container not just CLI, code-size limit.
11. **Health + logs** — stub `api/health.py`, unbounded `FileHandler` ignoring `log_level`.
12. **Chat perf** — per-token `setMessages` O(n²), scroll per frame, no virtualization.

---

## P2 — Docs / ops debt (CI deferred)

- No CI (`.github/` missing) — follow-up ticket, not P0 per decision.
- Stale `top_k=8` (`CONTEXT.md:38`, `tools/rag_query.py:3`, 4 test fakes) vs code `_DEFAULT_TOP_K=4`.
- Stale mega-prompt refs post ADR-032 (`providers/base.py:9`, `tools/base.py:8`, `useMessages.ts:192`).
- `SETUP.md` container ports `(5432,8000,80)` → should be `8080`.
- Loose `>=` pins, no `pytest-cov`, minimal ruff; `rip.ps1` vs `rip.sh` dotenv first-match vs last-wins.
- `frontend/.env` LAN IP → gitignored/local-only.

## Verification checklist

- [ ] F1/F2 manual + unit tests green (`pytest`, `ruff`).
- [ ] B1 disconnect + resume tested with 2 clients.
- [ ] B2 5-concurrent → 4 run + 1×429.
- [ ] B3 reboot reaper marks `error`.
- [ ] S1 cross-user 404 matrix passes.
- [ ] S2 login throttle + CORS with credentials passes.
- [ ] Architecture-changing items get `docs/ADR.md` entries.
