import type { ConfigEntry } from '@/types/admin'

function badgeClass(kind: 'live' | 'restart' | 'db' | 'env' | 'default'): string {
  switch (kind) {
    case 'live':
      return 'border-ledger/40 bg-ledger/[0.08] text-ledger'
    case 'restart':
      return 'border-brass-deep/40 bg-brass/[0.12] text-brass-deep'
    case 'db':
      return 'border-ink-soft/30 bg-paper text-ink-soft'
    case 'env':
      return 'border-line bg-card text-ink-soft/80'
    default:
      return 'border-line/70 bg-transparent text-ink-soft/60'
  }
}

export default function ConfigField({
  entry,
  draft,
  pending,
  onChange,
  onReset,
}: {
  entry: ConfigEntry
  draft: string | boolean
  pending: boolean
  onChange: (v: string | boolean) => void
  onReset: () => void
}) {
  const dirty =
    entry.editable &&
    !entry.secret &&
    String(draft) !== String(entry.value) &&
    !(typeof draft === 'boolean' && typeof entry.value === 'boolean' && draft === entry.value)

  return (
    <div className="flex flex-col gap-2 rounded-md border border-line/70 bg-paper/50 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-4">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[13px] font-semibold text-ink">{entry.label}</p>
          <span className={`font-ledger rounded-full border px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] ${badgeClass(entry.apply)}`}>
            {entry.apply === 'live' ? 'LIVE' : 'RESTART'}
          </span>
          <span className={`font-ledger rounded-full border px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] ${badgeClass(entry.source)}`}>
            {entry.source.toUpperCase()}
          </span>
          {pending && (
            <span className="font-ledger rounded-full border border-rust/50 bg-rust/[0.07] px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] text-rust">
              PENDING RESTART
            </span>
          )}
          {dirty && (
            <span className="font-ledger rounded-full border border-brass-deep/50 px-2 py-0.5 text-[9px] font-semibold tracking-[0.14em] text-brass-deep">
              EDITED
            </span>
          )}
        </div>
        <p className="mt-0.5 text-[12px] leading-snug text-ink-soft/75">{entry.description}</p>
        <p className="font-ledger mt-1 text-[10px] tracking-wide text-ink-soft/55">
          {entry.key} · default {String(entry.default)}
          {entry.updated_at ? ` · by ${entry.updated_by ?? 'admin'} ${new Date(entry.updated_at).toLocaleString()}` : ''}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {entry.secret || !entry.editable ? (
          <span className="font-ledger w-44 truncate rounded-md border border-line/60 bg-card px-2.5 py-1.5 text-[12px] text-ink-soft/60">
            {entry.secret ? (entry.configured ? '•••••• set' : 'not set') : String(entry.value)}
          </span>
        ) : entry.type === 'bool' ? (
          <button
            type="button"
            role="switch"
            aria-checked={draft === true}
            aria-label={entry.label}
            onClick={() => onChange(!(draft === true))}
            className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${draft === true ? 'bg-ledger' : 'bg-line'}`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-card shadow transition-all ${draft === true ? 'left-[22px]' : 'left-0.5'}`}
            />
          </button>
        ) : entry.type === 'enum' && entry.options ? (
          <select
            aria-label={entry.label}
            value={String(draft)}
            onChange={(e) => onChange(e.target.value)}
            className="h-9 w-44 rounded-md border border-line bg-card px-2 text-[13px] text-ink outline-none focus:border-ledger/70"
          >
            {entry.options.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        ) : (
          <input
            aria-label={entry.label}
            value={String(draft)}
            onChange={(e) => onChange(e.target.value)}
            inputMode={entry.type === 'str' ? 'text' : 'decimal'}
            spellCheck={false}
            className="font-ledger h-9 w-44 rounded-md border border-line bg-card px-2.5 text-[12px] text-ink outline-none placeholder:text-ink-soft/35 focus:border-ledger/70"
          />
        )}
        {entry.editable && !entry.secret && entry.source === 'db' && (
          <button
            type="button"
            onClick={onReset}
            title={`Reset ${entry.key} to default/env`}
            className="font-ledger text-[10px] font-semibold tracking-[0.14em] text-ink-soft/60 hover:text-rust"
          >
            RESET
          </button>
        )}
      </div>
    </div>
  )
}
