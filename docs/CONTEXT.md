# RIP — Research Intelligence Platform

RIP is an offline-capable, single-codebase research assistant that combines document RAG with multi-agent orchestration. Users upload documents into notebooks, then query them through a chat interface backed by local LLMs.

> **Design constraints** (endpoint signatures, schema DDL, config keys, forbidden patterns) live in `ARCHITECTURE.md` and `ADR.md`. This file is the domain glossary and data-flow reference only.

## Language

### Core Entities

**Notebook**:
A document collection with an attached chat history. One notebook = one conversation. The primary unit of organization; users create notebooks to scope their research.
_Avoid_: Conversation, project, workspace

**Run**:
An async task persisted to Postgres. Created when a user sends a message; survives page refresh; events streamed via SSE; only the stop button terminates it. Valid states: `pending`, `running`, `completed`, `failed`, `cancelled`.
_Avoid_: Orchestration unit, task, job, request

**SSE event**:
A single message in the Server-Sent Events stream for a run. Types: `run_started`, `plan`, `step_started`, `delta`, `step_completed`, `sources`, `summary`, `run_completed`, `artifacts`, `error`, `cancelled`. Structural events persisted to `run_events` for replay; `delta` is live-only (Q35).
_Avoid_: EventEnvelope, frame

**Message**:
A single chat turn (user or assistant) within a notebook. Linked to notebooks via `notebook_id` only. Written by the frontend only.
_Avoid_: Chat entry, turn, response

**Goal**:
The Planner's structured restatement of the user's intent. Derived from the user's message by the Planner LLM. The plan is built around a goal, not the raw message.
_Avoid_: Intent, objective, task

### Retrieval

**VectorRAG**:
The single retrieval path: vector similarity + Postgres full-text search combined by rank fusion, then reranked by BGE. Returns chunk-level context with source metadata.
_Avoid_: GraphRAG, AgenticRAG, retrieval pipeline

**rag.query**:
The tool that searches documents. Called as `rag.query(notebook_id, query, top_k=8)` reusing the lifespan `VectorRAG` singleton. On completion, the run worker emits an SSE `sources` event (Q32). Never used for verbatim file conversion.
_Avoid_: Search, retrieve, lookup

**notebook.inspect**:
The tool that lists a notebook's files (`{file_id, file_name, file_size, file_status}`). `notebook_id` injected like `rag.query`; called when the request refers to "this document" or the planner's file snapshot may be stale.
_Avoid_: files.list

**doc.convert**:
The tool that converts one uploaded PDF byte-for-byte to md, docx or pdf (lossless, no LLM, no search). Called as `doc.convert(file_id, target_format)`; `file_id` comes from the notebook snapshot or `notebook.inspect`, never invented. Distinct from `doc.generate` (report synthesis).
_Avoid_: convert, export

**Chunk**:
A segment of a parsed document, split on Markdown headers (`#/##/###`). Each chunk carries metadata (source file, H1/H2/H3 heading) and a vector.
_Avoid_: Passage, segment, slice

### Orchestration

**Planner**:
LLM-driven component that decomposes a user message into a goal and a plan — an ordered list of steps with dependencies (some run in parallel), each with an agent and tool assignment. Trivial/conversational requests yield a single `reasoning` step (never an empty plan — ADR-026).
_Avoid_: Router, dispatcher, coordinator

**Step**:
A single unit of work in the plan. Each step has an agent (or tool) assignment and may depend on other steps. Steps without dependencies run at the same time.
_Avoid_: PlanStep, task, unit

**Agent**:
A named capability (reasoning, coding, vision). Agents execute steps that require LLM reasoning. Each agent uses Ollama.
_Avoid_: AgentPlugin, model, brain

**Tool**:
A named function that performs a specific action: `rag.query` (search documents), `notebook.inspect` (list notebook files), `plot.chart` (make charts), `doc.generate` (make reports from answer text), `doc.convert` (exact PDF→md/docx/pdf conversion, lossless), `code.sandbox` (run code), `image.generate` (make images). Tools receive structured input and return structured output.
_Avoid_: ToolPlugin, function, capability

**Plan DAG**:
Ordered list of steps with dependencies the engine executes. Produced by the Planner, checked by the Validator, run by the Engine. Technical name for the dependency graph.
_Avoid_: Execution graph, workflow

**Validator**:
Deterministic check that agents/tools exist, dependencies have no cycles, and step budget is respected before execution. No approval gate — tools run directly.
_Avoid_: Checker, pre-validator

**Aggregator**:
Component that assembles step outputs into a final answer. Deterministic only (Q36) — no LLM synthesis step.
_Avoid_: Synthesizer, combiner

**Engine**:
Executor (built on LangGraph) that runs the plan steps in dependency order. No retry loop — failures are returned honestly.
_Avoid_: Executor, runner

**Orchestrator**:
Coordinator for the full run lifecycle: Planner → Engine → Aggregator → Memory. Manages state transitions and passes `notebook_id` through to tools.
_Avoid_: Coordinator, conductor, manager

**Artifact**:
A file produced by a tool that persists beyond the chat response (chart, document, image). Stored at `{UPLOAD_DIR}/{notebook_id}/artifacts/{run_id}/{step_id}/{filename}` (plus per-run `index.json`); surfaced via SSE `artifacts` event as download URLs (Q34). Charts (`kind: "chart"`, `image/svg+xml`) render inline in chat as `<img>` over the same URL — raw SVG markup is never injected into the DOM and never stored in the user-visible summary. Chart refs are also persisted on the assistant `messages.artifacts` by the frontend so previews survive reload. Distinct from `File` (user upload).
_Avoid_: Output, result, file

**File**:
A user-uploaded document in a notebook (`files` table, status `uploading/processing/ready/error`). Never confused with `Artifact` (tool output).
_Avoid_: Document, upload

**Source**:
A citation derived from a `Chunk`: `{source (file name), section (H1>H2>H3 path)}`. Delivered via SSE `sources` event after `rag.query`; persisted on assistant `messages.sources` by the frontend.
_Avoid_: Reference, citation

### Memory

**conversation_summary** (DB column):
Context-window-based compression of older messages, generated by Ollama when accumulated tokens reach ~70% of the model context window. Stored in `notebooks.conversation_summary` (internal, not user-visible). Distinct from the SSE `summary` event.
_Avoid_: summary (ambiguous), rolling summary

**SSE summary event**:
The final user-visible answer text streamed at end of run. Not stored in `notebooks.conversation_summary`.
_Avoid_: conversation_summary, compressed history

**Memory window**:
The set of recent messages kept verbatim alongside `conversation_summary`. Advisory cap (`memory_window_size=10`); real constraint is the token budget (`int(ollama_context_window=32768 * 0.7)` via `len//4` estimator, `summary_max_tokens=512`).
_Avoid_: Context window, message buffer

### Infrastructure

**Ollama**:
The sole LLM provider. Fully offline; no cloud dependencies. Default model: `qwen2.5:14b`.
_Avoid_: LLM provider, model backend

**VectorRAG embedding**:
Local models that turn document chunks into searchable vectors: BGE-M3 at `./backend/models/bge-m3`, reranker at `./backend/models/reranker/bge_reranker_v2_m3`.
_Avoid_: Embedding model, vector model

**Docling**:
Document parser that converts PDFs to Markdown before chunking.
_Avoid_: Parser, document converter

**Redis**:
Optional queue/cache. Runs are stored in Postgres, so Redis is not required to run.
_Avoid_: Queue, cache

### Observability

**Langfuse**:
External service that shows LLM/agent traces. Optional; not required to run.
_Avoid_: Tracing, monitoring, analytics

## Data Flow

### Message → Run → Response

1. Frontend persists user **message** via `POST /api/notebooks/{id}/messages`
2. Frontend calls `POST /v1/runs {notebook_id, message}` → `202 {run_id}`
3. Run worker loads `conversation_summary` + messages → `build_memory_context()` → starts orchestration with `context=`
4. **Planner** generates a **goal** and plan; **Engine** executes **steps** (parallel where possible)
5. `rag.query` receives `notebook_id` from the Run; worker emits SSE **sources** on completion
6. **Aggregator** assembles step outputs (deterministic, Q36); chart/SVG step outputs aggregate to a short placeholder — the SVG bytes travel via the SSE **artifacts** event only
7. Worker persists updated `conversation_summary` if memory folded new turns
8. SSE **artifacts** (download URLs; charts render inline as `<img>`) + **summary** (final answer) + **run_completed**; frontend persists assistant **message** with sources + artifacts
9. Structural SSE events persisted to `run_events`; `delta` tokens live-only

### conversation_summary Generation

After each run, the worker checks whether accumulated message tokens exceed ~70% of the model context window. If so, `memory.py` calls Ollama to fold aged-out turns into `notebooks.conversation_summary`. This is internal state — the frontend never renders it directly.
