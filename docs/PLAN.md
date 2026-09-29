# PLAN.md — Remaining Implementation

## 1. Backend Security Hardening

### 1.1 Path Traversal Fix
**File:** `backend/app/routes/files.py`
- Validate `notebook_id` against UUID regex pattern before using in `os.path.join`
- Reject with 400 if invalid
- Apply to all endpoints that accept `notebook_id` or `file_id` as path/form params

### 1.2 Upload Size Limit
**File:** `backend/app/core/config.py`
- Add `max_upload_size_mb: int = 50` setting
- Add `allowed_extensions: list[str] = [".pdf", ".docx", ".txt", ".md"]` setting

**File:** `backend/app/routes/files.py`
- Stream file in chunks, enforce size limit (50 MB)
- Validate file extension against whitelist
- Reject with 413 if too large, 415 if wrong type

### 1.3 Async File I/O
**File:** `backend/app/routes/files.py`
- Add `aiofiles` to `pyproject.toml` dependencies
- Replace `await file.read()` + blocking `open()/write()` with `aiofiles.open()` async streaming
- Write file in chunks to avoid memory spikes

---

## 2. Frontend Resilience

### 2.1 ErrorBoundary
**New file:** `frontend/src/components/ErrorBoundary.tsx`
- Class component with `componentDidCatch`
- Fallback UI: error message + "Reload" button (`window.location.reload()`)
- Wrap `<App />` in `main.tsx`
- Add route-level boundaries around `Notebook` page

### 2.2 SSE Reconnect
**File:** `frontend/src/services/runs.ts`
- Add exponential backoff reconnect: 1s → 2s → 4s → 8s → 16s
- Max 5 retries, then call `onError()` with "Connection lost"
- Pass `Last-Event-ID` header on reconnect for event replay
- Track retry count in closure, reset on successful connection

### 2.3 Race Condition Fix
**File:** `frontend/src/hooks/notebooks/useMessages.ts`
- Add `isRunningRef = useRef(false)` for synchronous guard check
- Set `isRunningRef.current = true` before any `await` in `handleSendMessage`
- Check `isRunningRef.current` instead of `isRunning` state
- Reset in `detach()` and useEffect cleanup

### 2.4 pastRuns LRU Cap
**File:** `frontend/src/hooks/notebooks/useMessages.ts`
- Cap `pastRuns` at 20 entries
- Evict oldest entry when limit reached (FIFO)

### 2.5 Incomplete Cleanup Fix
**File:** `frontend/src/hooks/notebooks/useMessages.ts`
- Reset all refs in useEffect cleanup: `textRef`, `sourcesRef`, `artifactsRef`, `stepResultsRef`, `planStepsRef`, `goalRef`

---

## 3. Docker Hardening

### 3.1 Resource Limits
**File:** `docker-compose.yml`
- Backend: `mem_limit: 8g`, `cpus: 2.0`
- Postgres: `mem_limit: 2g`, `cpus: 1.0`
- Frontend: `mem_limit: 512m`, `cpus: 0.5`

### 3.2 Logging Configuration
**File:** `docker-compose.yml`
- Add to all services:
  ```yaml
  logging:
    driver: "json-file"
    options:
      max-size: "10m"
      max-file: "3"
  ```

### 3.3 Network Isolation
**File:** `docker-compose.yml`
- Define custom bridge network `rip-net`
- Attach all services to `rip-net`
- Remove default bridge network

### 3.4 Frontend Non-Root
**File:** `frontend/Dockerfile`
- Use `nginx:1.27-alpine` base
- Run on port 8080 (nginx.conf updated)
- Add `setcap CAP_NET_BIND_SERVICE` to allow binding to 80 on host
- Use `USER nginx` for runtime
- Map host port 80 to container port 8080 in docker-compose

**File:** `frontend/nginx.conf`
- Change `listen 80` to `listen 8080`
- Add security headers:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Content-Security-Policy: default-src 'self'`
- Reduce `client_max_body_size` to 50M
- Reduce timeouts to 60s (except SSE endpoints)

### 3.5 Backend Dockerfile
**File:** `backend/Dockerfile`
- Pin Python patch version: `python:3.11-slim` → `python:3.11.9-slim`

---

## 4. Database Performance

### 4.1 Missing Indexes
**File:** `backend/schema.sql`
- Add `CREATE INDEX IF NOT EXISTS idx_files_notebook ON public.files (notebook_id);`
- Add `CREATE INDEX IF NOT EXISTS idx_embeddings_file ON public.embeddings (file_id);`

### 4.2 Connection Pooling
**File:** `backend/app/core/db.py`
- Add `ThreadedConnectionPool(minconn=2, maxconn=10)` from `psycopg2.pool`
- Replace per-request `psycopg2.connect()` with pool `getconn()` / `putconn()`
- Replace `print()` with `logger.error()` for error messages
- Add pool cleanup in application shutdown

---

## Execution Order

| Phase | Items | Dependencies |
|-------|-------|--------------|
| 1 | DB indexes + connection pooling | None |
| 2 | Backend security (path traversal, upload, async I/O) | None |
| 3 | Frontend resilience (ErrorBoundary, reconnect, race fix) | None |
| 4 | Docker hardening | Phase 2 (upload limits match nginx config) |

---

## Files Modified Summary

| File | Changes |
|------|---------|
| `backend/app/routes/files.py` | Path traversal, upload limits, async I/O |
| `backend/app/core/config.py` | Add upload settings |
| `backend/app/core/db.py` | Connection pooling, logging |
| `backend/schema.sql` | Add indexes |
| `pyproject.toml` | Add `aiofiles` dependency |
| `frontend/src/components/ErrorBoundary.tsx` | New file |
| `frontend/src/main.tsx` | Wrap in ErrorBoundary |
| `frontend/src/services/runs.ts` | SSE reconnect |
| `frontend/src/hooks/notebooks/useMessages.ts` | Race fix, LRU cap, cleanup |
| `docker-compose.yml` | Resource limits, logging, network |
| `frontend/Dockerfile` | Non-root user, port 8080 |
| `frontend/nginx.conf` | Security headers, timeouts, port |
| `backend/Dockerfile` | Pin Python version |
