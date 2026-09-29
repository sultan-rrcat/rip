# Changelog

Notable user-visible changes. Merge-era history (2026-09-08 → 2026-09-13) is frozen in `docs/archive/SESSION_LOG.md`.

---

## Unreleased — titled charts (trace `affdbbd4`)

- Charts no longer render untitled: every `plot.chart` carries a short title naming the metric and comparison (e.g. `mAP@50-95: FASDD_CV vs AgniNetra`). The step-by-step fallback asks for one in its prompt and refuses title-less proposals with a corrective hint before executing; direct calls without a title fall back to a labels-derived heading instead of a blank top margin.

## Unreleased — remove L0 fast-path and L3 mega-prompt (ADR-032)

- Planning is now L1 router (sole dispatcher, one call per request including greetings) → L2 deterministic builders → L3 step-by-step ReAct. The single-shot DAG prompt and its one-retry recall loop are gone: builder misses (unknown intent, `summarize_plot`, unresolvable converts, `>5` files) go straight to the ReAct loop (max 6 steps), and partial/failed runs fail honestly instead of replanning.
- Worst-path cost drops from router + 2 plans + 6 steps to router + 6 steps; greetings now spend one router call. The SSE `plan` event keeps a constant `attempt: 1` so old frontends keep working.

## Unreleased — no repeated plots (trace `07fb4f59`)

- "DocBench vs MMLongBench in a bar graph" no longer renders the same plot twice: the step-by-step fallback treats an exact repeat of a successful step as an idle turn, refuses to re-plot already-charted numbers under retitled/relabeled cosmetics, and rejects nested-`values` / `series_labels` chart shapes with a corrective hint before executing (previously two failed executions plus two duplicate charts in one run).
- Duplicate charts collapse downstream too: identical SVG bytes are collected as one artifact, and identical terminal outputs keep the first step shown. Chart successes log a one-line observation (never raw SVG), and the final synthesis describes charts plus a table instead of redrawing them as ASCII blocks.

## Unreleased — fail-closed summarize path (trace `cfbaa9c3`)

- "Summarize the docs" no longer leaks `{{1}} {{2}}` internals: a retrieval step that times out now resolves downstream placeholders to `(no chunks retrieved)` so the writer answers "not in the documents" (plus honest failure lines that trigger the one-retry recall) instead of asking the user to paste chunks. Terminal outputs still carrying `{{id}}` are demoted to failure lines by the aggregator rather than shown.
- Overview retrieval fits the timeout budget: `overview` shards get double the tool deadline (60s) and rerank at most `top_k*3` candidates (12 at the standard `top_k=4`) instead of the unbounded pool.
- The step-by-step fallback retrieves broadly and finishes with prose: summarize/compare/quiz asks default `rag.query` to `mode=overview`, and an exhausted loop synthesizes one grounded answer from its observations instead of returning a truncated raw chunk dump.

## Unreleased — empty-notebook routing (trace `ea48cb30`)

- Factual questions on a notebook with no ready documents no longer burn a retrieval step that provably returns nothing: `qa_single` answers generally from general knowledge (verbatim request, no document-grounding wrapper), while summarize/compare/quiz ask to upload or wait (single reasoning step, no `rag.query`). Files still processing yield a wait-and-retry clarification; an unreadable snapshot still attempts retrieval (the database, not the snapshot, is ground truth).
- The intent router now guarantees 1–3 search queries for document-grounded intents (post-filled from the request text when the model returns `[]`), and the mega-prompt carries an explicit zero-file rule so the L3 fallback cannot emit `rag.query` on an empty corpus. `qa_single` retrieval uses the unified `top_k: 4`.

## Unreleased — no-docs parametric plots + multi-series `plot.chart` (trace `27dcf635`)

- `plot.chart` now draws multi-series comparisons: shared `labels` plus `series: [{label, values}]` (at most 5 series, palette + legend; single-series `labels`+`values` unchanged). A plot request on a notebook with zero ready files no longer fails: the planner recalls approximate figures parametrically (one numbers-only step per series) and charts them with an "approximate" title instead of emitting an unguarded `plot.chart`.
- The step-by-step fallback stops repeating itself: `rag.query` is refused on an empty corpus, repeats of a failed executor become idle turns instead of re-executing, and `code.sandbox` is advertised as unavailable when the host has no docker CLI (previously 4 of 6 iterations burned on two empty retrievals and two `docker CLI not found` failures).

## Unreleased — ReAct input contract + plot preference (trace `c9e59039`)

- The step-by-step fallback no longer burns its 6-step budget on malformed tool inputs: `{"agent": {"message": …}}` shapes are normalized to flat fields (`message→query`/`code` aliases), stray `tool_id` keys dropped, and missing required fields get a correct-shape retry hint without executing. Bar/line chart asks route to `plot.chart` with literal numbers, never `code.sandbox`.
- L3 planner prompt unified to `top_k: 4` (stale `8`s removed) with an explicit both-branches-need-`{{1}}` rule for summarize+plot plans.

## Unreleased — file-scoped intelligent RAG (per-file shards + overview mode)

- `rag.query` is now file-aware: optional `file_id`/`file_name` scopes retrieval to one file (literals from the snapshot, never invented/placeholders), and `mode` selects `specific` (topical ranking, default) vs `overview` (stratified one-per-H1 sample in doc order with section-keyword boost). Default `top_k` is now 4 per shard.
- Deterministic builders fan out per file instead of per query-angle: `compare_multi` uses one `specific` shard per ready file (request text as query, so complexity/signal sections rank), `summarize`/`quiz` use one `overview` shard per file into a single reduce/writer step. `summarize` is now a deterministic intent; `>5` ready files fall through to the mega-prompt. Fixes the trace-observed single-document collapse on "compare both reports".
- Validator rejects empty/placeholder `file_id` and unknown `mode`; planner prompt documents the file-scoped compare/summarize/quiz rule. Snapshot `file_id` parsing now strips trailing `;`/`,` separators.

## Unreleased — layered planning (router + builders + ReAct fallback)

- Simple requests now skip the big planning prompt: greetings answer directly with no planning call, and single questions / compare-two-reports requests use a fixed, pre-validated plan shape (retrieval steps fanning into one grounded answer step) instead of asking the model to invent the wiring. The compare-two-reports failure from testing (answer asked the user to re-upload instead of reading the retrieved documents) is fixed by construction.
- When the planner still fails twice, the run now tries a step-by-step fallback (answer one small step at a time, up to 6 steps) before giving up; giving up still reports the original honest error.
- Validator also rejects plans whose answer step merely *mentions* "step 1/2" or "retrieved chunks" in prose without actually linking the retrieval steps, with a one-retry fix hint.
- Layered routing is now visible in Langfuse: a `router` span (sibling of `plan`, carrying intent/confidence/queries) shows which layer served the run (`L0-fast`/`L2-builder`/`L3-mega` on the `plan` span), and the step-by-step fallback traces as `react → react:iter-N → step:rN`. No behavior change; tracing stays opt-in.
- Router precedence: any plot/draw/chart ask now routes to `summarize_plot` (never `compare_multi`, which is comparisons with no chart), and fan-out queries are requested distinct. The trace-observed "compare + plot answered with matplotlib code and no chart" misroute is addressed at the classification layer.
- Deterministic builders for `quiz` (single-writer 2-step) and `convert_one`/`convert_all` (literal snapshot file ids, `"*"` for all; unresolvable falls through to the counter-question path). `summarize_plot` deliberately stays on the mega-prompt: chart labels are content-derived and no fixed shape may invent them.
- Retrieval diversity: reranked results round-robin by file, so multi-document requests see every file even when one dominates global ranking (verified live: both compare queries went from single-file to mixed-file results).

## Unreleased — auto-hoist wiring keys nested in `input`

- `Plan.from_model` now hoists `depends_on`/`expected_output_type`/`step_id` found inside a step's `input` up to top level (observed live: ornith-1.5:9b nests them twice running, burning the whole retry budget on an otherwise executable plan). Equal top-level copies are dropped as echoes; conflicting values stay a retry-actionable `ValueError` (differing input `step_id` keeps the "buries step" message). Planner Rule 4 documents the repair.

## Unreleased — thinking-model support (`think: false` on chat paths)

- `OllamaProvider._chat` / `_stream_payload` (`/v1/chat/completions`) now send `"think": false` like `generate_structured` already did: thinking models (e.g. `lfm2.5:latest`) otherwise spend the token budget on chain-of-thought and return empty answers. Pre-thinking-era servers that reject the unknown field get one retry without it. Note: `think: false` moves lfm2's thinking in-band (`<think>` blocks, stripped by `strip_think`/`ThinkFilter`) rather than disabling it — keep token budgets above ~512 so the answer fits after the thinking.

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
