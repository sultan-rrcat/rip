import type { PromptEntry } from '@/types/admin'

export default function PromptEditor({
  entry,
  draft,
  onChange,
  onReset,
  onSave,
  saving,
}: {
  entry: PromptEntry
  draft: string
  onChange: (v: string) => void
  onReset: () => void
  onSave: () => void
  saving: boolean
}) {
  const dirty = draft !== entry.value
  const lines = draft.split('\n').length
  const chars = draft.length

  return (
    <div className="rounded-md border border-line/70 bg-paper/50 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[13px] font-semibold text-ink">{entry.label}</p>
        <span className="font-ledger rounded-full border border-ledger/40 bg-ledger/[0.08] px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] text-ledger">
          LIVE
        </span>
        <span
          className={`font-ledger rounded-full border px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] ${
            entry.source === 'db'
              ? 'border-ink-soft/30 bg-paper text-ink-soft'
              : 'border-line/70 bg-transparent text-ink-soft/60'
          }`}
        >
          {entry.source.toUpperCase()}
        </span>
        {dirty && (
          <span className="font-ledger rounded-full border border-brass-deep/50 px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] text-brass-deep">
            EDITED
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[12px] leading-snug text-ink-soft/75">{entry.description}</p>
      {entry.placeholders.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5" aria-label="Required placeholders">
          {entry.placeholders.map((p) => {
            const present = draft.includes(p)
            return (
              <code
                key={p}
                title={present ? 'present' : 'MISSING — save will be refused'}
                className={`font-ledger rounded border px-1.5 py-0.5 text-[10px] ${
                  present
                    ? 'border-ledger/40 bg-ledger/[0.08] text-ledger'
                    : 'border-rust/50 bg-rust/[0.07] text-rust'
                }`}
              >
                {p}
              </code>
            )
          })}
        </div>
      )}
      <label htmlFor={`prompt-${entry.key}`} className="sr-only">
        {entry.label}
      </label>
      <textarea
        id={`prompt-${entry.key}`}
        value={draft}
        onChange={(e) => onChange(e.target.value)}
        rows={entry.placeholders.length > 0 ? 12 : 7}
        spellCheck={false}
        className="font-ledger mt-2 w-full rounded-md border border-line bg-card px-2.5 py-2 text-[12px] leading-relaxed text-ink outline-none placeholder:text-ink-soft/35 focus:border-ledger/70"
      />
      <div className="mt-1.5 flex items-center justify-between gap-3">
        <p className="font-ledger text-[10px] tracking-[0.14em] text-ink-soft/55">
          {entry.key} · {lines} LINES · {chars} CHARS
          {entry.updated_at ? ` · by ${entry.updated_by ?? 'admin'} ${new Date(entry.updated_at).toLocaleString()}` : ''}
        </p>
        <div className="flex shrink-0 items-center gap-2">
          {entry.source === 'db' && (
            <button
              type="button"
              onClick={onReset}
              disabled={saving}
              className="font-ledger text-[10px] font-semibold tracking-[0.14em] text-ink-soft/60 hover:text-rust disabled:opacity-40"
            >
              RESET
            </button>
          )}
          <button
            type="button"
            onClick={onSave}
            disabled={saving || !dirty}
            className="h-8 rounded-md bg-ink px-3 text-[12px] font-semibold text-paper hover:bg-ink-soft disabled:opacity-40"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
