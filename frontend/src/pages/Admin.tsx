import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import LogoutButton from '@/components/LogoutButton'
import SectionCard from '@/components/admin/SectionCard'
import ConfigField from '@/components/admin/ConfigField'
import { useRuntimeConfig } from '@/hooks/useRuntimeConfig'
import { getHostedModels } from '@/services/admin'
import type { HostedModel } from '@/types/admin'

type Drafts = Record<string, string | boolean>

function toDraft(raw: string | number | boolean): string | boolean {
  return typeof raw === 'boolean' ? raw : String(raw)
}

export default function Admin() {
  const { snapshot, loading, saving, error, setError, save, reset } = useRuntimeConfig()
  const [drafts, setDrafts] = useState<Drafts>({})
  const [confirmRestart, setConfirmRestart] = useState<null | { keys: string[]; values: Record<string, string | number | boolean> }>(null)
  const [notice, setNotice] = useState('')
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const [hostedModels, setHostedModels] = useState<HostedModel[]>([])
  const [modelsReachable, setModelsReachable] = useState(false)
  const [modelsLoading, setModelsLoading] = useState(false)

  const entries = snapshot?.entries ?? {}
  const groups = snapshot?.groups ?? []
  const pending = useMemo(() => new Set(snapshot?.pending_restart ?? []), [snapshot])

  useEffect(() => {
    if (!activeGroup && groups.length > 0) setActiveGroup(groups[0].id)
  }, [groups, activeGroup])

  const fetchModels = useCallback(async () => {
    setModelsLoading(true)
    try {
      const res = await getHostedModels()
      setHostedModels(res.models ?? [])
      setModelsReachable(res.reachable)
    } catch {
      setHostedModels([])
      setModelsReachable(false)
    } finally {
      setModelsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchModels()
  }, [fetchModels])

  const active = groups.find((g) => g.id === activeGroup) ?? groups[0]

  function draftFor(key: string): string | boolean {
    if (key in drafts) return drafts[key]
    const e = entries[key]
    return e ? toDraft(e.value as string | number | boolean) : ''
  }

  function dirtyKeysFor(fields: string[]): string[] {
    return fields.filter((k) => {
      const e = entries[k]
      if (!e || !e.editable || e.secret) return false
      if (!(k in drafts)) return false
      const d = drafts[k]
      if (typeof d === 'boolean' || typeof e.value === 'boolean') return d !== e.value
      return String(d) !== String(e.value)
    })
  }

  function pendingCountFor(fields: string[]): number {
    return fields.filter((k) => pending.has(k)).length
  }

  function coerceForSave(key: string, draft: string | boolean): string | number | boolean {
    const e = entries[key]
    if (!e) return draft
    if (e.type === 'bool') return draft === true
    if (e.type === 'int') return Number.parseInt(String(draft), 10)
    if (e.type === 'float') return Number.parseFloat(String(draft))
    return String(draft)
  }

  async function saveKeys(keys: string[]) {
    setNotice('')
    setError('')
    const updates: Record<string, string | number | boolean> = {}
    for (const k of keys) updates[k] = coerceForSave(k, draftFor(k))
    const restartTouched = keys.filter((k) => entries[k]?.apply === 'restart')
    if (restartTouched.length > 0 && !confirmRestart) {
      setConfirmRestart({ keys, values: updates })
      return
    }
    try {
      const snap = await save(updates)
      setDrafts((prev) => {
        const next = { ...prev }
        for (const k of keys) delete next[k]
        return next
      })
      const pend = snap.pending_restart.length > 0
        ? ` · ${snap.pending_restart.length} key(s) need \`${snap.pending_restart_command}\``
        : ''
      setNotice(`Saved ${keys.length} key(s)${pend}.`)
    } catch {
      // error already set by hook
    }
  }

  async function confirmSave() {
    if (!confirmRestart) return
    const { keys } = confirmRestart
    setConfirmRestart(null)
    // Bypass the guard on the second pass by calling save directly.
    setNotice('')
    const updates: Record<string, string | number | boolean> = {}
    for (const k of keys) updates[k] = coerceForSave(k, draftFor(k))
    try {
      const snap = await save(updates)
      setDrafts((prev) => {
        const next = { ...prev }
        for (const k of keys) delete next[k]
        return next
      })
      setNotice(`Saved ${keys.length} key(s) · restart-apply keys need \`${snap.pending_restart_command}\`.`)
    } catch {
      // hook sets error
    }
  }

  async function resetKeys(keys?: string[]) {
    setNotice('')
    try {
      await reset(keys)
      setDrafts((prev) => {
        const next = { ...prev }
        for (const k of keys ?? Object.keys(entries)) delete next[k]
        return next
      })
      setNotice(keys ? `Reset ${keys.length} key(s) to default/env.` : 'Reset all keys to default/env.')
    } catch {
      // hook sets error
    }
  }

  const allDirty = useMemo(() => dirtyKeysFor(Object.keys(entries)), [entries, drafts]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="h-screen overflow-y-auto bg-bench">
      <div className="mx-auto w-full max-w-5xl px-4 pb-10 pt-6 sm:px-6 sm:pt-10">
        <header className="rounded-xl bg-ink px-6 py-7 text-paper shadow-[0_18px_50px_-24px_rgba(21,39,54,0.7)] sm:px-8">
          <div className="flex items-start justify-between gap-4">
            <p className="font-ledger text-[11px] font-semibold tracking-[0.22em] text-paper/70">
              RIP · CONTROL DESK
            </p>
            <div className="flex items-center gap-3">
              <span className="font-ledger inline-flex items-center gap-2 rounded-full border border-paper/25 px-2.5 py-1 text-[10px] font-semibold tracking-[0.18em]">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" aria-hidden="true" />
                LOCAL ONLY
              </span>
              <span className="text-paper/80 [&_button]:text-paper">
                <LogoutButton />
              </span>
            </div>
          </div>
          <h1 className="font-display mt-5 max-w-xl text-3xl font-bold leading-[1.05] tracking-tight sm:text-4xl">
            Runtime variables.
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-relaxed text-paper/75">
            Live keys apply to the next request. Restart keys rebind on backend restart.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Link
              to="/"
              className="font-ledger text-[11px] font-semibold tracking-[0.18em] text-paper/80 hover:text-paper"
            >
              ← STACK
            </Link>
            <span className="font-ledger text-[11px] tracking-[0.14em] text-paper/60">
              {loading ? 'READING CONFIG…' : snapshot ? `${Object.keys(entries).length} KEYS · ${pending.size} PENDING RESTART` : ''}
            </span>
          </div>
        </header>

        {pending.size > 0 && (
          <div role="status" className="mt-4 rounded-lg border border-brass-deep/50 bg-brass/[0.12] px-5 py-4">
            <p className="font-ledger text-[11px] font-semibold tracking-[0.18em] text-brass-deep">
              RESTART REQUIRED — {pending.size} KEY(S)
            </p>
            <p className="mt-1 text-[13px] text-ink">
              {Array.from(pending).join(', ')} take full effect after{' '}
              <code className="font-ledger rounded bg-ink px-1.5 py-0.5 text-[11px] text-paper">
                {snapshot?.pending_restart_command ?? 'docker compose restart backend'}
              </code>
              . Values are already stored and will load on boot.
            </p>
          </div>
        )}

        {(error || notice) && (
          <div className="mt-4 space-y-2">
            {error && (
              <div role="alert" className="rounded-md border border-rust/50 bg-rust/[0.07] px-3 py-2.5 text-[13px] text-rust">
                <span className="font-ledger mb-0.5 block text-[10px] font-semibold tracking-[0.18em]">COULD NOT SAVE</span>
                {error}
              </div>
            )}
            {notice && (
              <div role="status" className="rounded-md border border-ledger/40 bg-ledger/[0.07] px-3 py-2.5 text-[13px] text-ledger">
                {notice}
              </div>
            )}
          </div>
        )}

        {loading && (
          <div className="mt-4 space-y-3" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="rounded-lg border border-line/70 bg-card/60 px-5 py-6">
                <div className="mx-auto mb-3 h-1.5 w-12 rounded-full bg-line/60" />
                <div className="h-4 w-2/5 rounded bg-line/50" />
                <div className="mt-2 h-3 w-1/4 rounded bg-line/40" />
              </div>
            ))}
          </div>
        )}

        {!loading && snapshot && active && (
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start">
            {/* Mobile: horizontal tab bar */}
            <nav aria-label="Config groups" className="flex gap-2 overflow-x-auto pb-1 sm:hidden">
              {groups.map((g) => {
                const dirty = dirtyKeysFor(g.fields).length
                const pend = pendingCountFor(g.fields)
                const isActive = g.id === active.id
                return (
                  <button
                    key={g.id}
                    type="button"
                    onClick={() => setActiveGroup(g.id)}
                    aria-current={isActive ? 'page' : undefined}
                    className={`flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 text-[12px] font-semibold transition-colors ${
                      isActive
                        ? 'border-ink bg-ink text-paper'
                        : 'border-line bg-card text-ink-soft hover:border-ink-soft/50'
                    }`}
                  >
                    {g.title}
                    {dirty > 0 && <span className={`h-1.5 w-1.5 rounded-full ${isActive ? 'bg-brass' : 'bg-brass-deep'}`} aria-label={`${dirty} unsaved`} />}
                    {pend > 0 && <span className="font-ledger text-[9px] font-bold text-rust">●</span>}
                  </button>
                )
              })}
            </nav>

            {/* Desktop: sidebar nav */}
            <nav aria-label="Config groups" className="hidden w-60 shrink-0 flex-col gap-1.5 sm:flex">
              {groups.map((g, gi) => {
                const dirty = dirtyKeysFor(g.fields).length
                const pend = pendingCountFor(g.fields)
                const isActive = g.id === active.id
                return (
                  <button
                    key={g.id}
                    type="button"
                    onClick={() => setActiveGroup(g.id)}
                    aria-current={isActive ? 'page' : undefined}
                    className={`drawer-row group rounded-lg border px-3 py-2.5 text-left transition-colors ${
                      isActive
                        ? 'border-ink bg-ink text-paper shadow-[0_8px_24px_-16px_rgba(21,39,54,0.6)]'
                        : 'border-line bg-card text-ink hover:border-ink-soft/50'
                    }`}
                  >
                    <span className={`font-ledger block text-[9px] font-semibold tracking-[0.2em] ${isActive ? 'text-paper/60' : 'text-ink-soft/55'}`}>
                      {String(gi + 1).padStart(2, '0')} · {g.apply.toUpperCase()}
                    </span>
                    <span className="mt-0.5 flex items-center gap-2">
                      <span className="font-display block flex-1 truncate text-[13px] font-semibold">{g.title}</span>
                      {dirty > 0 && (
                        <span className="font-ledger rounded-full bg-brass px-1.5 py-0.5 text-[9px] font-bold text-ink" title={`${dirty} unsaved`}>
                          {dirty}
                        </span>
                      )}
                      {pend > 0 && (
                        <span className="font-ledger rounded-full bg-rust px-1.5 py-0.5 text-[9px] font-bold text-paper" title={`${pend} pending restart`}>
                          {pend}↻
                        </span>
                      )}
                    </span>
                    <span className={`mt-0.5 block truncate text-[11px] ${isActive ? 'text-paper/65' : 'text-ink-soft/65'}`}>
                      {g.fields.length} keys
                    </span>
                  </button>
                )
              })}
            </nav>

            {/* Active group content — one section at a time, no long scroll */}
            <main className="min-w-0 flex-1" aria-label="Runtime configuration" aria-live="polite">
              {(() => {
                const editableFields = active.fields.filter((k) => entries[k]?.editable && !entries[k]?.secret)
                const visibleFields = active.fields.filter((k) => entries[k])
                const dirty = dirtyKeysFor(editableFields)
                const gi = groups.indexOf(active)
                return (
                  <SectionCard
                    key={active.id}
                    eyebrow={`GROUP ${String(gi + 1).padStart(2, '0')} / ${String(groups.length).padStart(2, '0')} · ${active.id.toUpperCase()}`}
                    title={active.title}
                    description={active.description}
                    apply={active.apply}
                    dirtyCount={dirty.length}
                    saving={saving}
                    onSave={editableFields.length > 0 ? () => saveKeys(dirty.length > 0 ? dirty : editableFields.filter((k) => k in drafts)) : undefined}
                    onReset={editableFields.length > 0 ? () => resetKeys(editableFields.filter((k) => entries[k]?.source === 'db')) : undefined}
                  >
                    {visibleFields.map((k) => (
                      <ConfigField
                        key={k}
                        entry={entries[k]}
                        draft={draftFor(k)}
                        pending={pending.has(k)}
                        onChange={(v) => setDrafts((prev) => ({ ...prev, [k]: v }))}
                        onReset={() => resetKeys([k])}
                        modelOptions={hostedModels}
                        modelsReachable={modelsReachable}
                        modelsLoading={modelsLoading}
                        onRefreshModels={fetchModels}
                      />
                    ))}
                  </SectionCard>
                )
              })()}
            </main>
          </div>
        )}

        {!loading && snapshot && (
          <footer className="mt-6 flex flex-wrap items-center justify-between gap-3">
            <p className="font-ledger text-[10px] tracking-[0.2em] text-ink-soft/60">
              WRITE-THROUGH · DB IS TRUTH, MEMORY SERVES
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={saving || allDirty.length === 0}
                onClick={() => saveKeys(allDirty)}
                className="h-9 rounded-md bg-ink px-4 text-[13px] font-semibold text-paper hover:bg-ink-soft disabled:opacity-40"
              >
                {saving ? 'Saving…' : `Save all (${allDirty.length})`}
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() => resetKeys()}
                className="h-9 rounded-md border border-line bg-card px-4 text-[13px] font-semibold text-ink-soft hover:bg-paper disabled:opacity-40"
              >
                Reset all to default
              </button>
            </div>
          </footer>
        )}

        <Dialog open={confirmRestart !== null} onClose={() => setConfirmRestart(null)} maxWidth="sm" fullWidth>
          <DialogTitle>Restart-apply keys included</DialogTitle>
          <DialogContent>
            <p className="text-[13px] leading-relaxed">
              {confirmRestart?.keys.filter((k) => entries[k]?.apply === 'restart').join(', ')} only take
              full effect after the backend restarts. The values will be stored now (DB is truth, they
              load on boot) and the page will flag them <b>PENDING RESTART</b>.
            </p>
            <p className="font-ledger mt-3 rounded bg-paper px-2 py-1.5 text-[11px]">
              {snapshot?.pending_restart_command ?? 'docker compose restart backend'}
            </p>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setConfirmRestart(null)}>Cancel</Button>
            <Button onClick={confirmSave} variant="contained" color="warning">
              Store anyway
            </Button>
          </DialogActions>
        </Dialog>
      </div>
    </div>
  )
}
