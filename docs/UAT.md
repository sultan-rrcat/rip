# UAT Process — end-user test suite (`backend/tests/test_uat.py`)

How the UAT suite is run, triaged, and what the live stack taught us.
Suite definition lives in `backend/tests/test_uat.py` (module docstring =
case map); this file records the *process*: environment, commands, log
monitoring, and the findings log from the first live runs (2026-10-01/02).

---

## 1. Prerequisites

| Need | Value on this host | Notes |
|---|---|---|
| Compose stack | `rip-postgres-1` (host `5433`→container `5432`), `rip-backend-1` (`8005`), `rip-frontend-1` | `docker ps`; backend/poetry env not needed — pytest drives the app in-process |
| Postgres | `127.0.0.1:5433`, db `rip`, user `rip` | `.env` already sets `DB_PORT=5433` (host `5432` is taken by a local `postgresql-x64-17` — never use bare defaults from a shell that didn't load `.env`) |
| Ollama | `http://localhost:11434` from the host | `.env` holds the *compose* value (`host.docker.internal`); host-side pytest must override (see §3) |
| Planner model | `granite4.1:3b-q4_K_M` (per `.env` `OLLAMA_DEFAULT_MODEL`) | Small/fast — routing behavior below is specific to this model; re-probe before trusting results on another model |
| BGE weights | `backend/models/bge-m3` + `reranker/` (CPU; lifespan loads once, ~1 min) | `TestClient(app)` lifespan loads them per session |
| Seed PDFs | `backend/tests/data/` (3 PDFs, committed fixtures) | Anomaly-Detection-Report, Indus-Faultbook-Assistant-Report, SQL-Server-Table-Partitioning |
| UAT user | `admin` / `admin` | DB seeds nothing — create once: `python scripts/create_user.py admin admin` (already-exists → done). Login is `POST /api/auth/login` → `rip_session` cookie. Local-UAT only, never production |

## 2. Auth pattern (why the suite logs in itself)

All `/api/*` + `/v1/*` (except health/auth) require `get_current_user`.
`conftest.py`'s shared `client` fixture never logs in, so it cannot create
notebooks against a real app. The UAT suite therefore:

1. Builds its own session `live_client` (`TestClient(app)` + lifespan) —
   deliberately *without* re-applying `schema.sql`: the bare
   `ALTER TABLE .. ADD CONSTRAINT embeddings_file_id_fkey` in
   `backend/schema.sql` is not re-runnable, so conftest's `_apply_schema`
   aborts every integration run as "already exists" and the whole module
   skips. The compose DB is migrated at deploy time; re-applying is wrong.
2. Logs in **once per session** (`admin`/`admin`, env-overridable
   `UAT_USERNAME`/`UAT_PASSWORD`) — the auth route allows 5 attempts/min/IP
   with a 15-min lockout, so per-test logins would risk locking the suite
   out. A 401 fails with the seed-command hint.
3. Creates one notebook per test (deleted in teardown) except three
   module-shared notebooks (`nb_empty`, `nb_single`, `nb_multi`) to avoid
   re-ingesting PDFs per test (ingest ≈ 2–4 min per PDF on this host:
   Docling/OpenDataLoader parse + CPU BGE embed).

## 3. Running

```powershell
# Single smoke test (~2–5 min: lifespan + 1 router + 1 reasoning call)
$env:DB_PORT = 5433
$env:OLLAMA_BASE_URL = "http://localhost:11434"
python -m pytest backend/tests/test_uat.py::TestEmptyNotebookChat::test_greeting -p no:warnings -q
# EXIT=0 is the signal (-q prints only dots)
```

Full suite (≈ 30–60 min, 15 tests) runs detached with logs to a file:

```powershell
# run_uat_full.ps1 sets the two env vars above, then:
#   python -m pytest backend/tests/test_uat.py -p no:warnings -q --tb=short
Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File <tmp>/run_uat_full.ps1" `
  -RedirectStandardOutput <tmp>/uat_full.log -RedirectStandardError <tmp>/uat_full.err `
  -WorkingDirectory C:\Users\offic\projects\rip-athena\rip
```

Monitor with `Get-Content <tmp>/uat_full.log` (dots = progress) and
`Get-Process python` (0 processes = finished). Without the live stack the
module self-skips (`UAT needs live stack (...)`) — 16 skipped, 0 errors is
the expected offline result.

## 4. Triage method (used for every failure below)

1. **Read the assertion first.** Most UAT failures are prompt/model
   behavior, not crashes — the assert line names the exact expectation
   (route intent, artifact presence, keyword set).
2. **Probe the router alone** (1 cheap LLM call, no DB writes) before
   re-running anything end-to-end:
   `Router(OllamaProvider()).route(text, notebook_context=snapshot)` over
   wording variants. This separates *routing* problems (fix prompt or
   product) from *execution* problems in seconds instead of minutes.
3. **Probe full runs with a kept notebook.** Suite teardown deletes
   notebooks (CASCADE deletes runs), destroying the evidence. The scratch
   probe (`probe_runs.py`: login → notebook → upload → run → print status,
   route, summary, per-step output/error, event types, artifacts) keeps the
   notebook and prints everything. Equivalent repeatable path without the
   script: run one test with `-x`, then query `runs`/`run_events` *before*
   teardown — teardown only runs after the test process ends, so in practice
   re-run via the probe script.
4. **Fix at the right layer:** test wording (ambiguous prompt) → suite;
   systematic model gap (empty slots) → product; infra flake (timeouts) →
   re-run to confirm before touching anything.

## 5. Findings log (first live runs)

| # | Symptom | Diagnosis (evidence) | Fix | Status |
|---|---|---|---|---|
| F1 | `test_plot_no_doc`: routed `knowledge_qa`, no chart | Router probe: combined explain+plot ask → `knowledge_qa` (builder = 1 reasoning step, never charts); pure `Draw a line chart ...` → `plot_standalone` | Suite: pure plot wording | ✅ pass |
| F2 | `test_summarize`: routed `qa_single` (3/3 phrasings) | Systematic on granite-3B, not wording — prompt order/content can't fix cheaply | Suite accepts `{summarize, qa_single}`; behavior (grounded summary + sources) still pinned | ✅ pass, model quirk recorded |
| F3 | `test_compare`: cites only Anomaly file | Faultbook shard returns nothing (`No reranked results passed threshold — using fallback` in logs; one probe run showed a `rag.query` shard timing out on CPU BGE) | Under diagnosis (probe aborted mid-run) — re-run §4-step-3 with compare + docs_plot cases | ⏳ open |
| F4 | `test_docs_plot`: no chart artifact | ReAct produced no `plot.chart` on this run; evidence lost to teardown | Same probe re-run as F3 | ⏳ open |
| F5 | `test_convert_no_doc_asks`: **failed** (was passing) | Slot recovery (F7) now fills `target_format=docx` on an empty notebook → `build_convert_all` ran `doc.convert "*"` with no files → 3 retries → run `failed`. Recovery made the empty case worse | Product: corpus guard in `build()` — `convert_one`/`convert_all` on `empty`/`processing` → upload/wait clarification (same as qa/compare/summarize/quiz/report) | ✅ fixed, awaiting re-run |
| F6 | `test_convert_one`/`test_convert_all`: no artifacts | Router probe: granite-3B returned intent with **blank slots on every convert phrasing** (`file_hint=''`, `target_format=''`) → counter-question instead of conversion | Product: `_recover_convert_slots` in `router.py` — filename from snapshot (exact, then stem), `*` for all/every-documents, format word via regex with filename spans masked (so `Report.pdf` never reads as `pdf`); intent untouched; LLM-filled slots win. 6 unit tests in `test_router.py` | ✅ pass E2E |
| F7 | `test_convert_no_doc_asks`: routed `convert_all` not `convert_ambiguous` | "Convert it to docx" → `convert_all` on small routers; on empty notebook both land in clarification | Suite accepts `{convert_ambiguous, convert_all}` | ✅ pass (pending re-run after F5 fix) |
| F8 | `test_verbatim_csv`: routed `compare_multi`, shard timeout, no CSV | Doc-grounded file asks take deterministic builders (no file step); knowledge-grounded file asks take `knowledge_qa` (no file step) — **no intent reaches the verbatim path E2E** (probed 3 phrasings) | Dropped from UAT; verbatim path stays covered by `TestDocGenerateVerbatim` unit tests + artifact test; knowledge+file intent = future work. Case map in suite docstring updated | ✅ closed by decision |
| – | ReAct intents report `route=None` | Builder misses return `plan_error → END` with no `plan` event, so `summarize_plot`/`plot_standalone` never surface an intent | Suite asserts behavior (artifacts/sources) instead; `_route_intent` documents this | ✅ by design |

Score at last full run: **12/15** (F3, F4 open; F5 fix unit-pinned —
`test_convert_all_empty_corpus_clarifies_instead_of_failing` and
`test_convert_all_processing_corpus_asks_to_wait` — live re-run skipped by
decision, pending a future window).

## 6. Known model behaviors (granite4.1:3b, reference only)

- Convert slots always blank → covered by `_recover_convert_slots`.
- `summarize ...` → `qa_single`; `convert it ...` → `convert_all`;
  composite explain+plot → `knowledge_qa`. Suite wording/tests accommodate;
  re-probe (`probe_router.py` pattern, §4-step-2) before changing model or
  prompts.
- CPU BGE retrieval is slow: `rag.query` shards can hit the step timeout
  and `rerank ... using fallback` appears in logs. Re-run suspected flakes
  before diagnosing.

## 7. Resume checklist (next session)

1. Re-run probe (`compare` + `docs_plot` cases, kept notebook) to close F3/F4.
2. Re-run full suite to confirm F5 fix live + reach 15/15.
3. Optional follow-ups (not started): `processing`-corpus ask, `>5` files →
   ReAct, frontend click-through of the same 9 journeys.
