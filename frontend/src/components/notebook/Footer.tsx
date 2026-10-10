import SendIcon from '@mui/icons-material/Send'
import StopIcon from '@mui/icons-material/Stop'
import { useState, useRef, useEffect, memo } from 'react'
import type {
  ChangeEvent,
  ClipboardEvent,
  KeyboardEvent,
} from 'react'

const CODE_PASTE_THRESHOLD = 200

interface FooterProps {
  onSendMessage: (text: string) => void
  isLoading?: boolean
  isRunning?: boolean
  onCancel?: () => void
}

const Footer = memo(function Footer({
  onSendMessage,
  isLoading,
  isRunning,
  onCancel,
}: FooterProps) {
  const [inputText, setInputText] = useState('')
  const [isCode, setIsCode] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    // Calculate based on scrollHeight but respect our max limit
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [inputText])

  function handleSend() {
    // Don't consume the draft while a run is in flight: the placeholder is
    // replaced by the Stop button, but Enter must not discard typed text.
    if (isLoading || !inputText.trim()) return
    onSendMessage(inputText)
    setInputText('')
    setIsCode(false)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function handlePaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const pasted = e.clipboardData.getData('text')
    const looksLikeCode =
      pasted.length > CODE_PASTE_THRESHOLD ||
      /(\n\s{2,}|\{|\}|=>|function |import |const |def |class )/.test(pasted)
    if (looksLikeCode) setIsCode(true)
  }

  function handleChange(e: ChangeEvent<HTMLTextAreaElement>) {
    setInputText(e.target.value)
    if (e.target.value === '') setIsCode(false)
  }

  return (
    <footer className="shrink-0 rounded-lg border border-line bg-card shadow-[0_1px_0_rgba(21,39,54,0.12)]">
      <div className="mx-auto w-full max-w-3xl px-4 py-3">
        <div className="rounded-md border border-line bg-paper/60 focus-within:border-ledger/60">
          <div className="relative px-3 py-2.5 ">
            <textarea
              ref={textareaRef}
              rows={1}
              value={inputText}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder="Ask about your sources"
              aria-label="Ask about your sources"
              className={`w-full resize-none overflow-y-auto border-none bg-transparent py-1.5 leading-relaxed text-ink outline-none placeholder:text-ink-soft/40
                            [&::-webkit-scrollbar]:w-1.5
                            [&::-webkit-scrollbar-track]:bg-transparent
                            [&::-webkit-scrollbar-thumb]:bg-line
                            [&::-webkit-scrollbar-thumb]:rounded-full
                            hover:[&::-webkit-scrollbar-thumb]:bg-ink-soft/40
                            [scrollbar-width:thin] [scrollbar-color:#b7c1cc_transparent]
                            ${isCode ? 'font-ledger text-[11px] whitespace-pre' : 'text-sm'}`}
              style={{
                minHeight: '32px',
                maxHeight: '200px',
                paddingRight: '2.5rem',
              }}
            />
          </div>
          <div className="flex items-center justify-between rounded-b-md bg-ink px-3 py-2">
            <div className="font-ledger flex items-center gap-2 text-[10px] font-semibold tracking-[0.18em] text-paper/70">
              <span
                className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400"
                aria-hidden="true"
              />
              LOCAL MODEL · OFFLINE
            </div>
            <div className="border-none">
              {isRunning ? (
                <button
                  onClick={onCancel}
                  title="Stop run"
                  aria-label="Stop run"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-rust text-white transition-colors hover:bg-rust/85"
                >
                  <StopIcon sx={{ fontSize: 16 }} />
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={isLoading || !inputText.trim()}
                  title="Send message"
                  aria-label="Send message"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-paper text-ink transition-all hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <SendIcon sx={{ fontSize: 16 }} />
                </button>
              )}
            </div>
          </div>
        </div>
        <p className="font-ledger mt-2 text-center text-[10px] tracking-wide text-ink-soft/50">
          SHIFT+ENTER FOR NEW LINE
        </p>
      </div>
    </footer>
  )
})

export default Footer
