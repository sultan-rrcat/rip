# Changelog

Notable user-visible changes. Merge-era history (2026-09-08 → 2026-09-13) is frozen in `docs/archive/SESSION_LOG.md`.

---

## Unreleased — auto-hoist wiring keys nested in `input`

- `Plan.from_model` now hoists `depends_on`/`expected_output_type`/`step_id` found inside a step's `input` up to top level (observed live: ornith-1.5:9b nests them twice running, burning the whole retry budget on an otherwise executable plan). Equal top-level copies are dropped as echoes; conflicting values stay a retry-actionable `ValueError` (differing input `step_id` keeps the "buries step" message). Planner Rule 4 documents the repair.

## Unreleased — parallel long-write cap raised to 5

- Validator `_check_parallel_fanout` now rejects 6+ (was 3+) parallel long-text agent steps off the same parent; the current model/host sustains at least 5 concurrent writes. Planner Rule 9 updated; MCQ single-writer shape stays the recommendation, per-level splits (up to 5) validate.

## Unreleased — planner structural hardening (stray elements, nested steps, omitted eot)

- Malformed plans fail with actionable retry feedback: stray non-object `steps[]` elements and whole steps buried inside another step's `input` are rejected naming the exact shape violation (previously a generic executor error the model repeated past the retry budget).
- Omitted `expected_output_type` on `rag.query` steps defaults to `"chunks"` (explicitly wrong values are still rejected), and validation retry feedback now leads with a per-step skeleton (id, executor, input keys) instead of a truncated JSON dump that cut off before the breakage.
- Planner Rule 1 now shows the exact forbidden shapes (bare `"step_id"` array elements, nested step objects).
- Grounded execution guards: dependent agent steps without a `{{id}}` placeholder, dangling placeholders, tool steps missing required input, and `rag.query` not typed `"chunks"` fail honestly at plan time; `{{id}}` placeholders auto-wire their `depends_on` edge.

## Unreleased — SHOW-persistent answers + collapsed-all Steps + visibility map

- Answer bubble is SHOW-only and persists: intermediate `rag.query` chunks can no longer leak into the final answer (validator now enforces `rag.query → expected_output_type "chunks"`; planner prompt reinforced).
- Steps panel lists all steps, each collapsed, and survives run completion (previously cleared on `run_completed` and rebuilt fully on replay); each step shows its `expected_output_type` and `shown/hidden` badge. The main bubble keeps showing only SHOW output.
- Langfuse observability: `aggregate` span now carries `shown`/`hidden`/`visibility`, and every `step:{id}` span carries `expected_output_type` + `visibility`; `step_completed`/`plan` SSE events carry the same fields (additive, old replays fall back to show/unknown).

## Unreleased — docs drift corrections

- Corrected stale docs against the code: README tool list now names all seven tools (adds `notebook.inspect`, `doc.convert`) and includes the `artifacts` SSE event; MUI version corrected to v9 (was v7) in `README.md` and `AGENT.md`; `AGENT.md` module map points at `code_sandbox.py` (was `code.sandbox.py`).
- Documented the `RAG_PDF_LOADER` switch (Docling default, OpenDataLoader alternative; the other loader is the fallback) in `SETUP.md`, `CONTEXT.md`, and `ARCHITECTURE.md`; added the `/api/notebooks/{id}/files`, `/api/files/upload`, `/api/files/{id}/status`, and `/api/files/{id}/process` routes to the `ARCHITECTURE.md` topology.

## Unreleased — grounded multi-step answers (ADR-023/027/020 amendments, run `4efaec2b`)

- "Summarize + plot" no longer loses the summary: the final answer keeps terminal text plus the chart placeholder (intermediate chunks/numbers stay hidden), and every live token streams into the main bubble.
- Ungrounded charts are refused instead of rendered: plans where a dependent `plot.chart` carries hardcoded values (or reads prose/chunks as numbers) fail honestly with a clear error; summarize+plot plans split numbers and answer branches.
- Per-step detail lives in the live-only Steps panel (no raw SVG dumps); tool steps no longer receive conversation history they ignore.
- Comparison plots fixed: garbled/multi-source `values` are rejected at plan time with a clear error instead of failing opaquely at render; placeholder-resolved comma-separated numbers are split back into chart points; hidden intermediates no longer leak into failure summaries.
- Bounded planner recall: rejected plans and partial/failed runs get one automatic retry with short, instance-specific feedback before failing honestly; clarifications never replan.

## Unreleased — dynamic document awareness (ADR-027)

- Planner now sees notebook files: factual questions over uploaded docs route to `rag.query` first instead of answering from parametric knowledge.
- New `notebook.inspect` tool (list files) and `doc.convert` tool (exact file→md/docx/pdf, lossless, no LLM/search). `doc.generate` stays report-only.
- Ambiguous convert requests ("convert it" with several files, or no format stated) yield a counter-question instead of a guessed conversion.
- `doc.convert` v2: DOCX support via python-docx, `file_id="*"` convert-all, `file_name` alias, per-file `conversions` list, stem-named artifacts, placeholder ban in planner prompt, aggregator surfaces partial failures.

## Unreleased — compose lifecycle scripts

- New `scripts/rip.ps1` (+ `scripts/rip.sh` mirror): `up` (always `--build`, waits healthy), `down`, `fresh` (wipes `pgdata` + `uploads` with confirm), `restart`, `rebuild`, `logs`, `ps`/`status`, `migrate` (re-applies `schema.sql` to running postgres, no host `psql` needed), `health`. Scripts clear stale shell `DB_*`, probe `127.0.0.1`, and read host ports from `.env`. Documented in `docs/SETUP.md` §4–5; `docs/AGENT.md` §5 points at them as canonical.

## Unreleased — chart rendering fix

- Charts (`plot.chart`) now render inline in chat as images over the artifact download URL instead of raw `<svg>` code. The run summary carries a short placeholder; the SVG bytes travel via the SSE `artifacts` event only.
- Chart previews persist on assistant messages (`messages.artifacts`) so they survive page reload. DB: re-apply `backend/schema.sql` via `psql` on existing databases (compose init runs once).
- Docs: corrected artifact disk path to `{upload_dir}/{notebook_id}/artifacts/{run_id}/{step_id}/{filename}` + download route `GET /v1/runs/{id}/artifacts/{artifact_id}` (ADR-019, CONTEXT, ARCHITECTURE); documented the aggregator SVG-placeholder exception (ADR-023).

## Unreleased — container/deploy hardening

- Compose: backend + frontend healthchecks (`/api/health`, `/healthz`),
  frontend gates on backend healthy, `extra_hosts` gateway for portable
  `host.docker.internal` (Windows/Linux), optional `.env` file.
- Backend image honors `$PORT`, stdlib `HEALTHCHECK`, proxy hygiene
  (`NO_PROXY` defaults; build proxy no longer persisted).
- Frontend nginx: real `proxy_cache off`/timeouts for SSE, `/healthz`,
  immutable `/assets/` vs no-cache `index.html`; new `frontend/.dockerignore`.
- Docs: host env canonical `ml_env` (`docs/AGENT.md` §5.1), `HOST_PG_PORT`
  sync + `psql` port fix, compose-init-once + same-origin notes.
- Docker hardening round 2: frontend `context: ./frontend` fix, `VITE_API_URL`
  build-arg, `PORT: 8000` pinned with `HOST_BACKEND_PORT`/`HOST_FRONTEND_PORT`
  host remaps, backend healthcheck grace 180s, Postgres `pg_isready` via
  container `$POSTGRES_USER`/`$POSTGRES_DB`, `OLLAMA_BASE_URL` interpolated
  from `.env`, nginx dual `listen` + `127.0.0.1` probes, reranker mount path
  `models/reranker/bge_reranker_v2_m3`, stale shell `DB_*` shadow documented.

- BGE path resolution (prior unreleased):
  Relative BGE model paths auto-resolve to repo-root absolute and fail fast.
- `DB_HOST` defaults to `127.0.0.1` (`localhost` auto-normalized), 5s connect timeout, password masked on connect failure.
- Ollama init degrades on 5s probe instead of boot-crashing; requests fail honest per call.
- Frontend uses same-origin API when `VITE_API_URL` is empty; nginx allows 100M uploads with unbuffered SSE.

## 2026-09-13 — Trivial-plan fallback

- Greetings and other trivial messages no longer fail with `No steps were executed`. The planner is instructed to emit a single `reasoning` step, and the engine repairs any still-empty plan the same way (`e58e18b`, ADR-026).

## 2026-09-13 — Docs reset as new project

- Archived `MERGE_PLAN.md`, `IMPLEMENTATION_PLAN.md`, `SESSION_LOG.md` to `docs/archive/` (frozen, history only).
- New `docs/ARCHITECTURE.md`; full `README.md` rewrite; curated `docs/ADR.md` (+ ADR-026); refreshed `docs/SETUP.md`; new `docs/CAVEATS.md`; `docs/AGENT.md` rewritten as maintainer guide.
