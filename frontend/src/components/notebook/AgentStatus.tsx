import { memo, useEffect, useState } from 'react'
import type { RunView } from '@/types/runs'

/** Human-readable verb per executor. Prefix match covers future tools. */
function labelForExecutor(executor: string): string {
  const e = executor.toLowerCase()
  if (e.includes('rag.query') || e.startsWith('rag')) return 'Searching sources'
  if (e.includes('code.read')) return 'Reading files'
  if (e.includes('code.sandbox')) return 'Running code'
  if (e.includes('coding') || e.startsWith('code')) return 'Working with code'
  if (e.includes('doc.generate')) return 'Drafting the document'
  if (e.includes('doc.convert')) return 'Converting files'
  if (e.includes('chart')) return 'Building the chart'
  if (e.includes('notebook.inspect')) return 'Inspecting the notebook'
  if (e.includes('reasoning')) return 'Reasoning over sources'
  return `Running ${executor}`
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
 */
const AgentStatus = memo(function AgentStatus({ run }: { run: RunView | null }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const steps = run?.plan ?? []
  const results = run?.stepResults ?? {}
  let label = 'Planning the inquiry'
  let stepSuffix: string | null = null

  if (steps.length > 0) {
    const runningStep =
      steps.find((s) => results[s.step_id]?.status === 'running') ??
      steps.find((s) => {
        const st = results[s.step_id]?.status
        return st !== 'done' && st !== 'success' && st !== 'failed' && st !== 'error'
      })
    if (runningStep) {
      label = labelForExecutor(runningStep.executor)
      const idx = steps.indexOf(runningStep) + 1
      stepSuffix = `STEP ${idx}/${steps.length}`
    } else {
      label = 'Drafting the answer'
      stepSuffix = `STEP ${steps.length}/${steps.length}`
    }
  }

  const elapsed =
    run?.startedAt != null ? formatElapsed(Math.max(0, now - run.startedAt)) : null
  const subline = [stepSuffix, elapsed].filter(Boolean).join(' · ')

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
