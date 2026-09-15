import SmartToyIcon from '@mui/icons-material/SmartToy'
import { useRef, useEffect, memo } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import type { Message, Source } from '@/types'
import type { Artifact, PlanStep, RunView } from '@/types/runs'
import ArtifactItem, { stripSvgFromText } from '@/components/notebook/ChartArtifact'

interface ChatAreaProps {
  messages: Message[]
  activeRun?: RunView | null
}

interface ChatAreaProps {
  messages: Message[]
}

const markdownComponents: Components = {
  // Custom renderer for code blocks (fenced blocks carry a language-* class;
  // inline code never does, so the class presence is the discriminator)
  code(props) {
    // Strip node/style: node must not leak onto the DOM and the inherited
    // HTML style prop conflicts with the highlighter's style theme type.
    // (Unused siblings of a rest element are exempt from noUnusedLocals.)
    const { children, className, node: _node, style: _style, ref: _ref, ...rest } = props
    const match = /language-(\w+)/.exec(className || '')
    return match ? (
      <SyntaxHighlighter
        style={oneLight as { [key: string]: CSSProperties }}
        language={match[1]}
        PreTag="div"
        {...rest}
      >
        {String(children).replace(/\n$/, '')}
      </SyntaxHighlighter>
    ) : (
      <code className={className}>{children}</code>
    )
  },
  // Ensure tables look good with Tailwind
  table({ children }) {
    return (
      <div className="overflow-x-auto my-2">
        <table className="border-collapse border border-gray-300 min-w-full">
          {children}
        </table>
      </div>
    )
  },
  th({ children }) {
    return (
      <th className="border border-gray-300 px-4 py-2 bg-gray-200">
        {children}
      </th>
    )
  },
  td({ children }) {
    return <td className="border border-gray-300 px-4 py-2">{children}</td>
  },
}

const ChatArea = memo(function ChatArea({ messages, activeRun }: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="m-1 space-y-6 flex-1 overflow-y-auto px-8 py-10 bg-white border border-gray-400 rounded-xl shadow-sm">
      {messages.map((message) => {
        // The in-flight run renders its plan + artifacts inside the run's
        // placeholder bubble; finished messages render from their rows.
        const runView =
          activeRun && activeRun.messageId === message.id ? activeRun : null
        if (message.role === 'assistant') {
          // Merge persisted message artifacts (reload-safe) with the
          // in-flight run's artifacts, deduped by artifact_id.
          const seen = new Set<string>()
          const artifacts: Artifact[] = [
            ...(message.artifacts ?? []),
            ...(runView?.artifacts ?? []),
          ].filter((a) => {
            if (seen.has(a.artifact_id)) return false
            seen.add(a.artifact_id)
            return true
          })
          return (
            <AssistantMessage
              key={message.id}
              text={message.text}
              sources={message.sources}
              plan={runView?.plan ?? null}
              goal={runView?.goal ?? null}
              artifacts={artifacts}
            />
          )
        }
        if (message.role === 'user') {
          return <UserMessage key={message.id} text={message.text} />
        }
        if (message.role === 'loading') {
          return (
            <AssistantMessage key={message.id}>
              <TypingIndicator />
            </AssistantMessage>
          )
        }
        if (message.role === 'error') {
          return <ErrorMessage key={message.id} text={message.text} />
        }
      })}
      <div ref={bottomRef} />
    </div>
  )
})

export default ChatArea

interface AssistantMessageProps {
  text?: string
  children?: ReactNode
  sources?: Source[]
  plan?: PlanStep[] | null
  goal?: string | null
  artifacts?: Artifact[]
}

function AssistantMessage({
  text,
  children,
  sources = [],
  plan = null,
  goal = null,
  artifacts = [],
}: AssistantMessageProps) {
  return (
    <div className="flex gap-4 items-start">
      {/* avatar  */}
      <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center shrink-0">
        <SmartToyIcon />
      </div>
      {/* content */}
      <div className="flex-1 space-y-1">
        <p className="text-xs font-small text-gray-300">Assistant</p>
        <div className="text-xs text-gray-600 leading-relaxed max-w-2xl border border-gray-200 p-4 rounded-xl rounded-tl-none bg-gray-100">
          {children ? (
            children
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={markdownComponents}
            >
              {stripSvgFromText(text)}
            </ReactMarkdown>
          )}

          {plan && plan.length > 0 && (
            <details className="mt-3 text-[11px] text-gray-500">
              <summary className="cursor-pointer font-semibold hover:text-gray-700">
                Plan{goal ? `: ${goal}` : ''}
              </summary>
              <ol className="mt-1 ml-4 list-decimal space-y-0.5">
                {plan.map((step) => (
                  <li key={step.step_id}>
                    {step.step_id} · {step.executor}
                    {step.depends_on.length > 0 &&
                      ` (after ${step.depends_on.join(', ')})`}
                  </li>
                ))}
              </ol>
            </details>
          )}

          {artifacts && artifacts.length > 0 && (
            <div className="mt-3 pt-2 border-t border-gray-300">
              <p className="text-[11px] font-semibold text-gray-500 mb-1">
                Artifacts
              </p>
              <ul className="space-y-2">
                {artifacts.map((a) => (
                  <li key={a.artifact_id} className="text-[11px]">
                    <ArtifactItem artifact={a} />
                  </li>
                ))}
              </ul>
            </div>
          )}

          {sources && sources.length > 0 && (
            <div className="mt-4 pt-3 border-t border-gray-300">
              <p className="text-[11px] font-semibold text-gray-500 mb-1">
                Sources
              </p>
              <ul>
                {sources.length > 0 &&
                  sources.map((s, i) => (
                    <li key={i} className="text-[11px] text-gray-500">
                      [{i + 1}] {s.source} - {s.section}
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

interface TextMessageProps {
  text: string
}

function UserMessage({ text }: TextMessageProps) {
  return (
    <div className="flex gap-4 items-start justify-end">
      <div className="flex-1 space-y-1 text-right">
        <p className="text-xs font-medium text-gray-400 ">You</p>
        <div className="inline-block text-gray-600 bg-blue-100 px-5 py-2 rounded-2xl rounded-tr-none text-xs leading-relaxed shadow-sm max-w-2xl">
          {text}
        </div>
      </div>
    </div>
  )
}

function ErrorMessage({ text }: TextMessageProps) {
  return (
    <div className="flex gap-4 items-start">
      <div className="w-8 h-8 rounded-lg bg-red-100 flex items-center justify-center shrink-0">
        <SmartToyIcon className="text-red-400" />
      </div>
      <div className="flex-1 space-y-1">
        <p className="text-xs font-medium text-red-300">Assistant</p>
        <div className="text-sm text-red-400 leading-relaxed max-w-2xl">
          {text}
        </div>
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-1 items-center h-5">
      <span
        className="w-1 h-1 bg-gray-700 rounded-full animate-bounce"
        style={{ animationDelay: '0ms' }}
      />
      <span
        className="w-1 h-1 bg-gray-700 rounded-full animate-bounce"
        style={{ animationDelay: '150ms' }}
      />
      <span
        className="w-1 h-1 bg-gray-700 rounded-full animate-bounce"
        style={{ animationDelay: '300ms' }}
      />
    </div>
  )
}
