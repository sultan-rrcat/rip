import { memo, useEffect, useState } from 'react'
import type { RunView } from '@/types/runs'

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`
}

/** Slim live-run readout: elapsed wall-clock + step progress. Rendered only while running. */
const RunStatusBar = memo(function RunStatusBar({ run }: { run: RunView }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const steps = run.plan ?? []
  const results = run.stepResults ?? {}
  const done = steps.filter((s) => {
    const st = results[s.step_id]?.status
    return st === 'done' || st === 'success' || st === 'failed' || st === 'error'
  }).length
  const runningStep = steps.find((s) => results[s.step_id]?.status === 'running')

  return (
    <div
      role="status"
      aria-live="polite"
      className="font-ledger flex shrink-0 items-center gap-3 rounded-lg border border-brass/50 bg-brass/10 px-3 py-1.5 text-[10px] font-semibold tracking-[0.14em] text-brass-deep"
    >
      <span className="inline-flex items-center gap-1.5">
        <span className="h-2 w-2 animate-pulse rounded-full bg-brass-deep" aria-hidden="true" />
        RUNNING{run.goal ? ` · ${run.goal.toUpperCase().slice(0, 40)}` : ''}
      </span>
      <span aria-hidden="true" className="text-brass-deep/40">·</span>
      <span>{formatElapsed(now - (run.startedAt ?? now))} ELAPSED</span>
      {steps.length > 0 && (
        <>
          <span aria-hidden="true" className="text-brass-deep/40">·</span>
          <span>
            STEP {Math.min(done + 1, steps.length)}/{steps.length}
            {runningStep ? ` · ${runningStep.executor.toUpperCase()}` : ''}
          </span>
          <span
            className="h-1 min-w-16 flex-1 overflow-hidden rounded-full bg-brass/25"
            role="progressbar"
            aria-valuenow={done}
            aria-valuemin={0}
            aria-valuemax={steps.length}
            aria-label="Run progress"
          >
            <span
              className="block h-full rounded-full bg-brass-deep transition-all"
              style={{ width: `${steps.length > 0 ? Math.round((done / steps.length) * 100) : 0}%` }}
            />
          </span>
        </>
      )}
    </div>
  )
})

export default RunStatusBar
