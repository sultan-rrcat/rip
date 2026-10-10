import Card from '@/components/home/Card'
import LogoutButton from '@/components/LogoutButton'
import { useState, useEffect, useMemo } from 'react'
import { getNotebooksAPI } from '@/services/notebooks'
import type { Notebook } from '@/types'

function shelfmark(index: number): string {
  return `RIP-${String(index + 1).padStart(2, '0')}`
}

function ledgerLine(nb: Notebook): string {
  const date = nb.created_at
    ? new Date(nb.created_at).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    : 'undated'
  const short = nb.notebook_id.slice(0, 6).toUpperCase()
  return `${date} · ${short}`
}

export default function Home() {
  const [notebooks, setNotebooks] = useState<Notebook[]>([])
  const [query, setQuery] = useState('')
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getNotebooksAPI()
      .then((data) => {
        if (!cancelled && Array.isArray(data)) {
          setNotebooks(data)
          setFailed(false)
        }
      })
      .catch((err) => {
        console.error('Failed to load notebooks:', err)
        if (!cancelled) setFailed(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const sorted = useMemo(
    () =>
      [...notebooks].sort((a, b) =>
        (b.created_at ?? '').localeCompare(a.created_at ?? ''),
      ),
    [notebooks],
  )

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return sorted
    return sorted.filter((n) => n.notebook_name.toLowerCase().includes(q))
  }, [sorted, query])

  function retry() {
    setFailed(false)
    setLoading(true)
    getNotebooksAPI()
      .then((data) => {
        if (Array.isArray(data)) setNotebooks(data)
      })
      .catch((err) => {
        console.error('Failed to load notebooks:', err)
        setFailed(true)
      })
      .finally(() => setLoading(false))
  }

  return (
    <div className="h-screen overflow-y-auto bg-bench">
      <div className="mx-auto w-full max-w-3xl px-4 pb-10 pt-6 sm:px-6 sm:pt-10">
        {/* cabinet header: the thesis is the offline stack itself */}
        <header className="rounded-xl bg-ink px-6 py-7 text-paper shadow-[0_18px_50px_-24px_rgba(21,39,54,0.7)] sm:px-8">
          <div className="flex items-start justify-between gap-4">
            <p className="font-ledger text-[11px] font-semibold tracking-[0.22em] text-paper/70">
              RIP · RESEARCH INTELLIGENCE PLATFORM
            </p>
            <div className="flex items-center gap-3">
              <span className="font-ledger inline-flex items-center gap-2 rounded-full border border-paper/25 px-2.5 py-1 text-[10px] font-semibold tracking-[0.18em]">
                <span
                  className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400"
                  aria-hidden="true"
                />
                LOCAL ONLY
              </span>
              <span className="text-paper/80 [&_button]:text-paper">
                <LogoutButton />
              </span>
            </div>
          </div>
          <h1 className="font-display mt-5 max-w-xl text-3xl font-bold leading-[1.05] tracking-tight sm:text-4xl">
            Every inquiry in its drawer.
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-relaxed text-paper/75">
            Open a drawer to resume. Sources stay on this machine.
          </p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
            <label htmlFor="drawer-search" className="sr-only">
              Search drawers
            </label>
            <input
              id="drawer-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search drawers"
              className="font-ledger h-10 w-full flex-1 rounded-md border border-paper/20 bg-ink-soft px-3 text-[13px] text-paper placeholder:text-paper/40 focus:border-paper/60 focus:outline-none sm:max-w-xs"
            />
            <p className="font-ledger text-[11px] tracking-[0.14em] text-paper/60">
              {loading
                ? 'READING STACK…'
                : `${visible.length} DRAWER${visible.length === 1 ? '' : 'S'}`}
            </p>
          </div>
        </header>

        {/* drawer stack */}
        <main className="mt-4 space-y-3" aria-label="Notebooks">
          <Card isNew={true} title={'Create New'} />
          {loading &&
            [0, 1, 2].map((i) => (
              <div
                key={i}
                className="rounded-lg border border-line/70 bg-card/60 px-5 py-6"
                aria-hidden="true"
              >
                <div className="mx-auto mb-3 h-1.5 w-12 rounded-full bg-line/60" />
                <div className="h-4 w-2/5 rounded bg-line/50" />
                <div className="mt-2 h-3 w-1/4 rounded bg-line/40" />
              </div>
            ))}
          {!loading &&
            !failed &&
            visible.map((n, i) => (
              <Card
                key={n.notebook_id}
                isNew={false}
                title={n.notebook_name}
                id={n.notebook_id}
                shelfmark={shelfmark(sorted.indexOf(n))}
                ledger={ledgerLine(n)}
                index={i}
                setNotebooks={setNotebooks}
                onDelete={(id) => {
                  setNotebooks((prev) =>
                    prev.filter((nb) => nb.notebook_id !== id),
                  )
                }}
              />
            ))}
          {!loading && !failed && visible.length === 0 && notebooks.length > 0 && (
            <div className="rounded-lg border border-line bg-card px-5 py-8 text-center">
              <p className="font-display text-[15px] font-semibold text-ink">
                No drawer matches “{query.trim()}”.
              </p>
              <p className="mt-1 text-[13px] text-ink-soft/75">
                Clear the search to see the full stack.
              </p>
            </div>
          )}
          {!loading && !failed && notebooks.length === 0 && (
            <div className="rounded-lg border border-line bg-card px-5 py-8 text-center">
              <p className="font-display text-[15px] font-semibold text-ink">
                The stack is empty.
              </p>
              <p className="mt-1 text-[13px] text-ink-soft/75">
                Start the first drawer above — name it after the question, not
                the files.
              </p>
            </div>
          )}
          {!loading && failed && (
            <div className="rounded-lg border border-rust/50 bg-card px-5 py-8 text-center">
              <p className="font-display text-[15px] font-semibold text-ink">
                Could not read the stack.
              </p>
              <p className="mt-1 text-[13px] text-ink-soft/75">
                Check the backend connection, then try again.
              </p>
              <button
                type="button"
                onClick={retry}
                className="mt-4 h-9 rounded-md bg-ink px-4 text-[13px] font-semibold text-paper hover:bg-ink-soft"
              >
                Retry
              </button>
            </div>
          )}
        </main>

        <footer className="mt-6 flex items-center justify-between">
          <p className="font-ledger text-[10px] tracking-[0.2em] text-ink-soft/60">
            OFFLINE · NOTHING LEAVES THIS MACHINE
          </p>
          <span className="block h-px w-24 bg-line" aria-hidden="true" />
        </footer>
      </div>
    </div>
  )
}
