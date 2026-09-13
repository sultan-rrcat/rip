// types/runs.ts — RunEvent discriminated union (MERGE_PLAN.md §Frontend, Q7 locked).
// Copied verbatim; `seq: number` on every variant is the reconnect dedupe key.
//
// Two backend realities 5.3 must normalize when parsing (kept out of the
// locked shape on purpose):
// - `delta` frames arrive with fractional STRING seqs ("3.1") — live-only,
//   never replayed (Q35). Compare dedupe keys via String(seq).
// - `step_started` carries `executor_id` (not agent_id/tool_id) and `error`
//   carries `message` (not `error`); extra fields are ignored by the union.

export type Source = { source: string; section: string }
export type Artifact = { artifact_id: string; kind: string; filename: string; url: string }

// One plan step as emitted by the SSE `plan` event.
export type PlanStep = { step_id: string; executor: string; depends_on: string[] }

export type RunEvent =
  | { type: 'run_started'; run_id: string; seq: number }
  | { type: 'plan'; goal: string; steps: PlanStep[]; seq: number }
  | { type: 'step_started'; step_id: string; agent_id?: string; tool_id?: string; seq: number }
  | { type: 'delta'; step_id: string; content: string; seq: number }   // live only — not replayed from DB
  | { type: 'step_completed'; step_id: string; status: string; output?: string; seq: number }
  | { type: 'sources'; sources: Source[]; seq: number }
  | { type: 'summary'; content: string; seq: number }
  | { type: 'run_completed'; status: string; seq: number }
  | { type: 'artifacts'; artifacts: Artifact[]; seq: number }
  | { type: 'error'; error: string; seq: number }
  | { type: 'cancelled'; reason: string; seq: number }

// Live view-model for the in-flight run, kept alongside messages[] by
// useMessages. The plan/sources/artifacts render inside the run's
// placeholder assistant bubble; on run_completed the placeholder is swapped
// for the persisted message and the view is cleared.
export interface RunView {
  runId: string
  messageId: string
  goal: string | null
  plan: PlanStep[] | null
  sources: Source[]
  artifacts: Artifact[]
  running: boolean
}
