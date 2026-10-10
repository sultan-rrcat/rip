import { useEffect } from 'react'

export default function ConfirmModal({
  open,
  title,
  eyebrow,
  onCancel,
  onConfirm,
  confirmLabel,
  children,
}: {
  open: boolean
  title: string
  eyebrow: string
  onCancel: () => void
  onConfirm: () => void
  confirmLabel: string
  children: React.ReactNode
}) {
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md overflow-hidden rounded-xl border border-line bg-card shadow-[0_24px_60px_-20px_rgba(21,39,54,0.8)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="bg-ink px-5 pb-4 pt-4 text-paper">
          <div className="flex justify-center pb-3" aria-hidden="true">
            <span className="block h-1.5 w-12 rounded-full bg-brass" />
          </div>
          <p className="font-ledger text-[10px] font-semibold tracking-[0.22em] text-paper/60">{eyebrow}</p>
          <h2 className="font-display mt-1 text-lg font-bold tracking-tight">{title}</h2>
        </div>
        <div className="px-5 py-4 text-[13px] leading-relaxed text-ink">{children}</div>
        <div className="flex items-center justify-end gap-2 border-t border-line/70 px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="h-9 rounded-md border border-line px-4 text-[13px] font-semibold text-ink-soft hover:bg-paper"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="h-9 rounded-md bg-rust px-4 text-[13px] font-semibold text-paper hover:opacity-90"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
