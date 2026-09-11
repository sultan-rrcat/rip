# Project Plan (`PLAN.md`)

Status reflects the repository as implemented. Checkbox states are verified against the code; the "fix" items below correspond one-to-one with the numbered entries in the [Known Issues & Deviations](ARCHITECTURE.md#known-issues--deviations) section of `docs/ARCHITECTURE.md`.

---

## Phase 0: Current state

The end-to-end prototype works: create notebook → upload PDF → background ingestion → streamed Q&A with source listing. Remaining work is largely hardening and fixing known bugs rather than greenfield features.

### Stability / correctness fixes (highest priority)
- [x] Fix `/api/prompt` non-stream endpoint: pass `notebook_id` to `VectorRAG.retrieve_context` (Known Issue #2).
- [x] Align the env-var naming: decide on one key (`LLM_URL`) and remove the unused `LLM_API_URL` read (Known Issue #1).
- [x] Fix frontend port mismatch: route the `/process` call through `src/config.js` instead of hardcoded `localhost:5000` (Known Issue #3).
- [x] Fix `uploadFileAPI` error branch (undefined `uuidv4`/`setFiles`) (Known Issue #4).
- [x] Fix `useMessages` error branch using `id` instead of `notebook_id` (Known Issue #5).
- [x] Decide and document the LLM endpoint + model contract (OpenAI-compatible `/v1/chat/completions`, `qwen2.5-coder-14b`) as the single source of truth. **Done:** documented in `SETUP.md`, `ARCHITECTURE.md`, `ADR.md`, and `Readme.md`; live behavior pinned by the prompt integration tests.

## Phase 1: Ingestion & retrieval hardening
- [x] Make model paths configurable via env (remove hardcoded `D:\models\...`) (Known Issue #7). **Done:** `BGE_M3_MODEL_PATH`/`BGE_RERANKER_V2_M3` read from env with local fallbacks.
- [x] Preserve the real uploaded file extension instead of always storing `.pdf` (Known Issue #7). **Done:** upload + ingestion derive `{ext}` from the filename (`.pdf` fallback). Type validation still open.
- [ ] Re-enable or remove the disabled semantic chunker (`rag/pipeline.py`); tune chunking for equations/tables.
- [ ] Add durable ingestion status/job tracking (currently a background task with no retry or progress) — relates to ADR-005.
- [x] Expand backend tests beyond the health check (Known Issue #9). **Done:** 14 integration tests against the real stack (DB + models + APIs, no mocks); prompt tests skip when the LLM is unreachable. Note: tests need local models + DB by design (per decision: no mocks).

## Phase 2: Dead code & cleanup
- [x] Remove the `AgenticRAG` stub (removed along with the graph RAG stack; see ADR-007).
- [x] Remove or wire the unused `BaseRAG` (Known Issue #6; see ADR-006). **Done:** `rag/base.py` deleted; `VectorRAG` extends `RagPipeline` directly.
- [x] Remove unused `all-MiniLM-L6-v2` and Ollama config entries (Known Issue #6). **Done:** only live keys remain in `config.py`; commented-out semantic chunker removed from `pipeline.py`.
- [x] Delete unused UI components (`Header`, `RightSidebar`, `Main`, `Notification`) (Known Issue #8). **Done.** Unused API clients still open.
- [x] Rename `embeddings_test` to `embeddings` (Known Issue #10). **Done** across schema + code; migration comment added in `schema.sql`. Files-list response shape normalization still open.

## Phase 3: Feature roadmap
- [ ] Robust source citation & highlighting in chat responses.
- [ ] Notebook document management polish (drag-and-drop upload, per-file status/progress indicators).
- [ ] Local evaluation harness for retrieval accuracy and end-to-end answer quality.

## Phase 4: Offline evaluation & benchmarking
- [ ] Performance profiling of local `qwen2.5-coder-14b` inference and streaming latency.
- [ ] Latency budgets for ingestion (Docling parsing + embedding) at realistic document volumes.
