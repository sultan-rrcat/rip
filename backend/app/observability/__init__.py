"""Observability for RIP: optional Langfuse tracing (Athena port).

Everything here is a no-op when tracing is disabled
(`langfuse_enabled=False`), so enabling/disabling tracing never changes run
behavior. When enabled, the run worker opens one trace per run (session =
notebook, mirroring Athena's session = conversation), the engine emits
plan/aggregate spans, plan_graph emits per-step spans, and the
TracingProvider tags every LLM call as a generation — all nested under the
run trace, even across the plan graph's worker threads (contexts are copied
per node from the worker thread, so Langfuse contextvars ride along).
"""
