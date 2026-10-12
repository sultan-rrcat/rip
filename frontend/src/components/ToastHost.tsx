import { memo, useEffect, useState } from 'react'
import CloseIcon from '@mui/icons-material/Close'
import { TOAST_EVENT } from '@/services/toast'

interface Toast {
  id: number
  message: string
}

let nextId = 1
const MAX_TOASTS = 3
const DISMISS_MS = 4000

/** Fixed bottom-right stack. Mount once per page (see Notebook). */
const ToastHost = memo(function ToastHost() {
  const [toasts, setToasts] = useState<Toast[]>([])

  useEffect(() => {
    function onToast(e: Event) {
      const message = (e as CustomEvent<string>).detail
      if (!message) return
      const id = nextId++
      setToasts((prev) => [...prev.slice(-(MAX_TOASTS - 1)), { id, message }])
      window.setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, DISMISS_MS)
    }
    window.addEventListener(TOAST_EVENT, onToast)
    return () => window.removeEventListener(TOAST_EVENT, onToast)
  }, [])

  if (toasts.length === 0) return null

  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-72 flex-col gap-2"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className="toast-enter pointer-events-auto flex items-start gap-2 rounded-lg border border-line bg-ink px-3 py-2.5 text-[13px] leading-snug text-paper shadow-lg"
        >
          <span className="min-w-0 flex-1">{t.message}</span>
          <button
            type="button"
            onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
            aria-label="Dismiss notification"
            className="rounded p-0.5 text-paper/70 hover:text-paper"
          >
            <CloseIcon sx={{ fontSize: 14 }} />
          </button>
        </div>
      ))}
    </div>
  )
})

export default ToastHost
