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
- [ ] Decide and document the LLM endpoint + model contract (OpenAI-compatible `/v1/chat/completions`, `qwen2.5-coder-14b`) as the single source of truth.

## Phase 1: Ingestion & retrieval hardening
- [ ] Make model paths configurable via env (remove hardcoded `D:\models\...`) (Known Issue #7).
- [ ] Preserve the real uploaded file extension instead of always storing `.pdf`; validate supported types (Known Issue #7).
- [ ] Re-enable or remove the disabled semantic chunker (`rag/pipeline.py`); tune chunking for equations/tables.
- [ ] Add durable ingestion status/job tracking (currently a background task with no retry or progress) — relates to ADR-005.
- [ ] Expand backend tests beyond the health check; make them runnable without local models (Known Issue #9).

## Phase 2: Dead code & cleanup
- [x] Remove the `AgenticRAG` stub (removed along with the graph RAG stack; see ADR-007).
- [ ] Remove or wire the unused `BaseRAG` (Known Issue #6; see ADR-006).
- [ ] Remove unused `all-MiniLM-L6-v2` and Ollama config entries (Known Issue #6).
- [ ] Mount or delete unused UI components (`Header`, `RightSidebar`, `Main`, `Notification`) and unused API clients (Known Issue #8).
- [ ] Rename `embeddings_test` to a non-test name and normalize the files list response shape (Known Issue #10).

## Phase 3: Feature roadmap
- [ ] Robust source citation & highlighting in chat responses.
- [ ] Notebook document management polish (drag-and-drop upload, per-file status/progress indicators).
- [ ] Local evaluation harness for retrieval accuracy and end-to-end answer quality.

## Phase 4: Offline evaluation & benchmarking
- [ ] Performance profiling of local `qwen2.5-coder-14b` inference and streaming latency.
- [ ] Latency budgets for ingestion (Docling parsing + embedding) at realistic document volumes.
