import type { ReactNode } from 'react'

export default function SectionCard({
  eyebrow,
  title,
  description,
  apply,
  dirtyCount,
  onSave,
  onReset,
  saving,
  children,
}: {
  eyebrow: string
  title: string
  description: string
  apply: 'live' | 'mixed' | 'system'
  dirtyCount: number
  onSave?: () => void
  onReset?: () => void
  saving: boolean
  children: ReactNode
}) {
  const applyLabel = apply === 'live' ? 'APPLIES LIVE' : apply === 'mixed' ? 'MIXED — SOME NEED RESTART' : 'READ-ONLY'
  return (
    <section className="rounded-lg border border-line bg-card shadow-[0_1px_0_rgba(21,39,54,0.12),0_8px_24px_-16px_rgba(21,39,54,0.4)]">
      <div className="flex justify-center pt-2.5" aria-hidden="true">
        <span className="brass-pull block h-1.5 w-12 rounded-full bg-brass" />
      </div>
      <div className="px-4 pb-2 pt-1 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-ledger text-[10px] font-semibold tracking-[0.2em] text-ink-soft/60">{eyebrow}</p>
            <h2 className="font-display mt-0.5 text-[16px] font-semibold text-ink">{title}</h2>
            <p className="mt-0.5 text-[12px] text-ink-soft/75">{description}</p>
          </div>
          <span className="font-ledger shrink-0 rounded-full border border-line px-2 py-1 text-[9px] font-semibold tracking-[0.16em] text-ink-soft/70">
            {applyLabel}
          </span>
        </div>
      </div>
      <div className="space-y-2 px-4 pb-3 sm:px-5">{children}</div>
      {(onSave || onReset) && (
        <div className="flex items-center justify-between gap-3 border-t border-line/70 px-4 py-2.5 sm:px-5">
          <p className="font-ledger text-[10px] tracking-[0.16em] text-ink-soft/60">
            {dirtyCount > 0 ? `${dirtyCount} UNSAVED` : 'ALL SAVED'}
          </p>
          <div className="flex items-center gap-2">
            {onReset && (
              <button
                type="button"
                onClick={onReset}
                disabled={saving}
                className="h-8 rounded-md border border-line px-3 text-[12px] font-semibold text-ink-soft hover:bg-paper disabled:opacity-40"
              >
                Reset section
              </button>
            )}
            {onSave && (
              <button
                type="button"
                onClick={onSave}
                disabled={saving || dirtyCount === 0}
                className="h-8 rounded-md bg-ink px-4 text-[12px] font-semibold text-paper hover:bg-ink-soft disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save section'}
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
