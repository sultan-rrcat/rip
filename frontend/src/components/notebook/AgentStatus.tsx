import { memo, useEffect, useRef, useState } from 'react'
import type { PlanStep, RunView, StepResultView } from '@/types/runs'

// After this long on one step, append "still working…" to the subline.
// The per-step clock is local (resets on refresh — step start isn't
// persisted), so this is a liveness hint only, never displayed as time.
const STALL_MS = 20_000

const TERMINAL = new Set(['done', 'success', 'failed', 'error'])
const HIDDEN_EOT = new Set(['chunks', 'numbers', 'quiz_json'])

function isHidden(step: PlanStep, result: StepResultView | undefined): boolean {
  if (result?.visibility === 'hide') return true
  const eot = result?.expected_output_type ?? step.expected_output_type
  return eot != null && HIDDEN_EOT.has(eot)
}

/** Human-readable verb per executor. Generic verbs only — never the goal. */
function labelForExecutor(executor: string): string {
  const e = executor.toLowerCase()
  if (e.includes('rag.query') || e.startsWith('rag')) return 'Searching sources'
  if (e.includes('code.read')) return 'Reading files'
  if (e.includes('code.sandbox')) return 'Running code'
  if (e.includes('notebook.inspect')) return 'Inspecting the notebook'
  if (e.includes('doc.generate')) return 'Drafting the document'
  if (e.includes('doc.convert')) return 'Converting files'
  if (e.includes('chart')) return 'Building the chart'
  if (e.includes('quiz')) return 'Preparing questions'
  if (e.includes('reasoning')) return 'Reasoning over sources'
  if (e.includes('coding') || e.startsWith('code')) return 'Working with code'
  return 'Working'
}

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`
}

/**
 * Single in-flight indicator: one brass ring spinner + a live label derived
 * from the run's chain-of-thought (plan + step states). Replaces the old
 * bouncing-dots placeholder and the top progress bar.
 *
 * Minimal subline by design: `STEP i/n · total elapsed`, plus a
 * `still working…` hint when one step runs long. No goal text, no
 * per-step seconds, no source/artifact counts.
 */
const AgentStatus = memo(function AgentStatus({ run }: { run: RunView | null }) {
  const [now, setNow] = useState(() => Date.now())
  // Local start of the currently-running step (wall clock, never shown).
  const stepStartRef = useRef<{ id: string | null; t: number }>({ id: null, t: 0 })

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const steps = run?.plan ?? []
  const results = run?.stepResults ?? {}
  const attempt = run?.attempt ?? 1

  // Hidden intermediates never shift the visible STEP i/n numbering. When
  // every step is hidden, fall back to the full plan to avoid STEP 0/0.
  const visible = steps.filter((s) => !isHidden(s, results[s.step_id]))
  const numbered = visible.length > 0 ? visible : steps
  const total = numbered.length

  const running = numbered.filter((s) => results[s.step_id]?.status === 'running')
  const nextPending =
    running.length === 0
      ? numbered.find((s) => !TERMINAL.has(results[s.step_id]?.status ?? 'pending'))
      : undefined
  const allTerminal =
    numbered.length > 0 && numbered.every((s) => TERMINAL.has(results[s.step_id]?.status ?? 'pending'))

  const focalId = running.length > 0 ? running[0].step_id : null
  useEffect(() => {
    if (stepStartRef.current.id !== focalId) {
      stepStartRef.current = { id: focalId, t: Date.now() }
    }
  }, [focalId])
  const stalled = focalId != null && now - stepStartRef.current.t >= STALL_MS

  let label = 'Planning the inquiry'
  let stepSuffix: string | null = null

  if (steps.length === 0) {
    label = attempt > 1 ? 'Replanning' : 'Planning the inquiry'
  } else if (running.length > 1) {
    label = `Working on ${running.length} steps`
    stepSuffix = `STEP ${numbered.indexOf(running[0]) + 1}/${total}`
  } else if (running.length === 1) {
    // A retry in flight reads as its step, not as a special state: once a
    // step of attempt N is running, the work is the step itself.
    label = labelForExecutor(running[0].executor)
    stepSuffix = `STEP ${numbered.indexOf(running[0]) + 1}/${total}`
  } else if (nextPending) {
    // Retry recall lands here before the first step_started: a second+
    // attempt with nothing running yet is honestly "Replanning", while a
    // first attempt anticipates its next step so the label never gaps.
    if (attempt > 1) {
      label = 'Replanning'
    } else {
      label = labelForExecutor(nextPending.executor)
    }
    stepSuffix = `STEP ${numbered.indexOf(nextPending) + 1}/${total}`
  } else if (allTerminal) {
    label = 'Drafting the answer'
    stepSuffix = `STEP ${total}/${total}`
  }

  const elapsed =
    run?.startedAt != null ? formatElapsed(Math.max(0, now - run.startedAt)) : null
  const subline = [stepSuffix, elapsed, stalled ? 'still working…' : null]
    .filter(Boolean)
    .join(' · ')

  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 py-1">
      <span
        aria-hidden="true"
        className="agent-spin inline-block h-4 w-4 shrink-0 rounded-full border-2 border-line border-t-brass-deep"
      />
      <div className="min-w-0">
        {/* key remounts on label change so the fade replays per step */}
        <p key={label} className="agent-label truncate text-[13px] font-medium text-ink-soft">
          {label}…
        </p>
        {subline && (
          <p className="font-ledger mt-0.5 text-[10px] tracking-[0.14em] text-ink-soft/55">
            {subline}
          </p>
        )}
      </div>
    </div>
  )
})

export default AgentStatus
