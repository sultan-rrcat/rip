import { useState } from 'react'
import type { ToolEntry } from '@/types/admin'

export default function ToolConfigRow({
  entry,
  referencedBy,
  descDraft,
  onDescChange,
  onDescSave,
  onDescReset,
  onToggle,
  saving,
}: {
  entry: ToolEntry
  referencedBy: string[]
  descDraft: string
  onDescChange: (v: string) => void
  onDescSave: () => void
  onDescReset: () => void
  onToggle: (enabled: boolean) => void
  saving: boolean
}) {
  const [editing, setEditing] = useState(false)
  const descDirty = descDraft !== entry.description

  return (
    <div
      className={`rounded-md border px-3 py-2.5 ${
        entry.enabled ? 'border-line/70 bg-paper/50' : 'border-rust/40 bg-rust/[0.04]'
      }`}
    >
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          role="switch"
          aria-checked={entry.enabled}
          aria-label={`Enable ${entry.tool_id}`}
          onClick={() => onToggle(!entry.enabled)}
          disabled={saving}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-40 ${
            entry.enabled ? 'bg-ledger' : 'bg-line'
          }`}
        >
          <span
            className={`absolute top-0.5 h-5 w-5 rounded-full bg-card shadow transition-all ${
              entry.enabled ? 'left-[22px]' : 'left-0.5'
            }`}
          />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-ledger text-[13px] font-semibold text-ink">{entry.tool_id}</p>
            <span className="text-[12px] text-ink-soft/75">{entry.name}</span>
            <span className="font-ledger rounded-full border border-line px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] text-ink-soft/70">
              {entry.effect_class.toUpperCase()}
            </span>
            {!entry.enabled && (
              <span className="font-ledger rounded-full border border-rust/50 bg-rust/[0.07] px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] text-rust">
                DISABLED
              </span>
            )}
            {entry.description_source === 'db' && (
              <span className="font-ledger rounded-full border border-ink-soft/30 bg-paper px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] text-ink-soft">
                EDITED
              </span>
            )}
          </div>
          <p className="font-ledger mt-0.5 text-[10px] tracking-wide text-ink-soft/55">
            USED BY · {referencedBy.join(' · ')}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing((v) => !v)}
          aria-expanded={editing}
          className="h-8 shrink-0 rounded-md border border-line bg-card px-3 text-[12px] font-semibold text-ink-soft hover:bg-paper"
        >
          {editing ? 'Hide text' : 'Edit text'}
        </button>
      </div>
      {editing && (
        <div className="mt-2">
          <label htmlFor={`tool-desc-${entry.tool_id}`} className="sr-only">
            Description for {entry.tool_id}
          </label>
          <textarea
            id={`tool-desc-${entry.tool_id}`}
            value={descDraft}
            onChange={(e) => onDescChange(e.target.value)}
            rows={5}
            spellCheck={false}
            className="w-full rounded-md border border-line bg-card px-2.5 py-2 text-[12px] leading-relaxed text-ink outline-none focus:border-ledger/70"
          />
          <div className="mt-1.5 flex items-center justify-between gap-3">
            <p className="font-ledger text-[10px] tracking-[0.14em] text-ink-soft/55">
              MENU TEXT · {descDraft.length} CHARS
              {entry.updated_at ? ` · by ${entry.updated_by ?? 'admin'} ${new Date(entry.updated_at).toLocaleString()}` : ''}
            </p>
            <div className="flex shrink-0 items-center gap-2">
              {entry.description_source === 'db' && (
                <button
                  type="button"
                  onClick={onDescReset}
                  disabled={saving}
                  className="font-ledger text-[10px] font-semibold tracking-[0.14em] text-ink-soft/60 hover:text-rust disabled:opacity-40"
                >
                  RESET
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  onDescSave()
                  setEditing(false)
                }}
                disabled={saving || !descDirty}
                className="h-8 rounded-md bg-ink px-3 text-[12px] font-semibold text-paper hover:bg-ink-soft disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save text'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
