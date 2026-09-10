import SendIcon from '@mui/icons-material/Send'
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
}

const Footer = memo(function Footer({ onSendMessage, isLoading }: FooterProps) {
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
    if (!inputText.trim()) return
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
    <footer className="m-1 mt-0 py-4 shrink-0 bg-white border border-gray-400 rounded-xl shadow-sm">
      <div className="w-full max-w-3xl mx-auto px-4">
        <div className="border border-gray-400 rounded-xl">
          <div className="relative px-3 py-2.5 ">
            <textarea
              ref={textareaRef}
              rows={1}
              value={inputText}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder="Enter your query"
              disabled={isLoading}
              className={`w-full bg-transparent border-none outline-none text-gray-700 placeholder:text-gray-400 resize-none overflow-y-auto leading-relaxed py-1.5
                            [&::-webkit-scrollbar]:w-1.5
                            [&::-webkit-scrollbar-track]:bg-transparent
                            [&::-webkit-scrollbar-thumb]:bg-gray-300
                            [&::-webkit-scrollbar-thumb]:rounded-full
                            hover:[&::-webkit-scrollbar-thumb]:bg-gray-400
                            [scrollbar-width:thin] [scrollbar-color:#d1d5db_transparent]
                            ${isCode ? 'text-[11px] font-mono whitespace-pre' : 'text-sm'}`}
              style={{
                minHeight: '32px',
                maxHeight: '200px',
                paddingRight: '2.5rem',
              }}
            />
          </div>
          <div className="flex justify-between bg-gray-100 px-3 py-3 rounded-xl">
            <div className="flex items-center font-medium text-sm">
              Qwen 2.5
            </div>
            <div className="border-none">
              <button
                onClick={handleSend}
                disabled={isLoading || !inputText.trim()}
                className="flex items-center justify-center w-8 h-8 bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg shrink-0 transition-all"
              >
                <SendIcon sx={{ fontSize: 16 }} />
              </button>
            </div>
          </div>
        </div>
        <p className="text-[10px] text-gray-400 text-center mt-2">
          Shift+Enter for new line
        </p>
      </div>
    </footer>
  )
})

export default Footer
