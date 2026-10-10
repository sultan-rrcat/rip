import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
        credentials: 'include',
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Login failed')
      }

      onLogin()
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="h-screen overflow-y-auto bg-bench">
      <div className="mx-auto flex min-h-full w-full max-w-md flex-col justify-center px-4 py-10">
        {/* gate header */}
        <header className="rounded-t-xl bg-ink px-6 pb-6 pt-6 text-paper">
          <div className="flex items-center justify-between gap-3">
            <p className="font-ledger text-[10px] font-semibold tracking-[0.22em] text-paper/70">
              RIP · RESEARCH INTELLIGENCE PLATFORM
            </p>
            <span className="font-ledger inline-flex shrink-0 items-center gap-2 rounded-full border border-paper/25 px-2.5 py-1 text-[10px] font-semibold tracking-[0.18em]">
              <span
                className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400"
                aria-hidden="true"
              />
              LOCAL ONLY
            </span>
          </div>
          <h1 className="font-display mt-4 text-3xl font-bold leading-none tracking-tight">
            Open the stack.
          </h1>
          <p className="mt-2 text-[13px] leading-relaxed text-paper/70">
            Sign in on this machine. Sources stay here.
          </p>
        </header>

        {/* drawer front form */}
        <form
          onSubmit={handleSubmit}
          className="rounded-b-xl border border-t-0 border-line bg-card px-6 py-6 shadow-[0_18px_50px_-24px_rgba(21,39,54,0.5)]"
        >
          <div className="flex justify-center pb-4" aria-hidden="true">
            <span className="block h-1.5 w-12 rounded-full bg-brass" />
          </div>

          {error && (
            <div
              role="alert"
              className="mb-4 rounded-md border border-rust/50 bg-rust/[0.07] px-3 py-2.5 text-[13px] leading-snug text-rust"
            >
              <span className="font-ledger mb-0.5 block text-[10px] font-semibold tracking-[0.18em]">
                COULD NOT SIGN IN
              </span>
              {error}
            </div>
          )}

          <div className="flex flex-col gap-4">
            <div>
              <label
                htmlFor="login-username"
                className="font-ledger mb-1.5 block text-[10px] font-semibold tracking-[0.18em] text-ink-soft/70"
              >
                USERNAME
              </label>
              <input
                id="login-username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                required
                autoComplete="username"
                placeholder="e.g. field-notes"
                className="h-10 w-full rounded-md border border-line bg-paper/60 px-3 text-sm text-ink outline-none placeholder:text-ink-soft/35 focus:border-ledger/70"
              />
            </div>

            <div>
              <label
                htmlFor="login-password"
                className="font-ledger mb-1.5 block text-[10px] font-semibold tracking-[0.18em] text-ink-soft/70"
              >
                PASSWORD
              </label>
              <input
                id="login-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                placeholder="••••••••"
                className="h-10 w-full rounded-md border border-line bg-paper/60 px-3 text-sm text-ink outline-none placeholder:text-ink-soft/35 focus:border-ledger/70"
              />
            </div>

            <button
              type="submit"
              disabled={loading || !username || !password}
              className="font-display mt-1 h-10 w-full rounded-md bg-ink text-[15px] font-semibold text-paper transition-colors hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-40"
            >
              {loading ? 'Opening…' : 'Sign in'}
            </button>
          </div>
        </form>

        <footer className="mt-5 flex items-center justify-between px-1">
          <p className="font-ledger text-[10px] tracking-[0.2em] text-ink-soft/60">
            OFFLINE · NOTHING LEAVES THIS MACHINE
          </p>
          <span className="block h-px w-16 bg-line" aria-hidden="true" />
        </footer>
      </div>
    </div>
  )
}
