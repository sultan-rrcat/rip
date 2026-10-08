import SmartToyIcon from '@mui/icons-material/SmartToy'
import { useRef, useLayoutEffect, useEffect, memo } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import type { Message, Source } from '@/types'
import type { Artifact, PlanStep, RunView } from '@/types/runs'
import ArtifactItem from '@/components/notebook/ChartArtifact'
import { stripSvgFromText } from '@/components/notebook/artifact'
import TypingDots from '@/components/notebook/TypingDots'

interface ChatAreaProps {
  messages: Message[]
  activeRun?: RunView | null
  pastRuns?: Record<string, RunView>
}

const markdownComponents: Components = {
  // Headings: Tailwind preflight resets h1-h6 to inherit (same size/weight
  // as body text), so without explicit classes every heading renders at the
  // parent's text-xs size. Give each level a distinct type scale.
  h1({ children }) {
    return (
      <h1 className="mt-4 mb-2 text-xl font-bold leading-tight text-gray-900">
        {children}
      </h1>
    )
  },
  h2({ children }) {
    return (
      <h2 className="mt-4 mb-2 text-lg font-bold leading-tight text-gray-900">
        {children}
      </h2>
    )
  },
  h3({ children }) {
    return (
      <h3 className="mt-3 mb-1.5 text-base font-semibold leading-snug text-gray-900">
        {children}
      </h3>
    )
  },
  h4({ children }) {
    return (
      <h4 className="mt-3 mb-1 text-sm font-semibold leading-snug text-gray-900">
        {children}
      </h4>
    )
  },
  h5({ children }) {
    return (
      <h5 className="mt-2 mb-1 text-sm font-semibold text-gray-800">
        {children}
      </h5>
    )
  },
  h6({ children }) {
    return (
      <h6 className="mt-2 mb-1 text-xs font-semibold uppercase tracking-wide text-gray-700">
        {children}
      </h6>
    )
  },
  p({ children }) {
    return <p className="my-2 text-sm leading-relaxed">{children}</p>
  },
  a({ children, href }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="text-blue-600 underline underline-offset-2 hover:text-blue-800"
      >
        {children}
      </a>
    )
  },
  // Lists: preflight also strips bullets/numbers and margins.
  ul({ children }) {
    return <ul className="my-2 ml-5 list-disc space-y-1 text-sm">{children}</ul>
  },
  ol({ children }) {
    return (
      <ol className="my-2 ml-5 list-decimal space-y-1 text-sm">{children}</ol>
    )
  },
  li({ children }) {
    return <li className="leading-relaxed">{children}</li>
  },
  blockquote({ children }) {
    return (
      <blockquote className="my-2 border-l-4 border-gray-300 pl-3 italic text-gray-600">
        {children}
      </blockquote>
    )
  },
  hr() {
    return <hr className="my-4 border-gray-200" />
  },
  strong({ children }) {
    return (
      <strong className="font-semibold text-gray-800">{children}</strong>
    )
  },
  // Fenced blocks render as <pre><code class="language-*">. The inner code
  // branch below handles highlighting; reset the outer <pre> so the
  // highlighter's own box is the visible frame (no double margins).
  pre({ children }) {
    return (
      <pre className="my-3 overflow-x-auto rounded-lg text-[13px] [&>div]:!m-0">
        {children}
      </pre>
    )
  },
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
        customStyle={{ margin: 0, fontSize: '13px' }}
        {...rest}
      >
        {String(children).replace(/\n$/, '')}
      </SyntaxHighlighter>
    ) : (
      <code
        className={`rounded bg-gray-200 px-1 py-0.5 font-mono text-[0.85em] text-gray-800 ${className ?? ''}`}
      >
        {children}
      </code>
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

const ChatArea = memo(function ChatArea({ messages, activeRun, pastRuns }: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // Only stick to the bottom while the user is already near it, so scrolling
  // up to read mid-stream isn't yanked back on every token.
  const stickToBottomRef = useRef(true)
  // Coalesce per-token autoscrolls to one scroll per frame. scrollIntoView
  // is intentionally avoided: it scrolls every ancestor including the
  // document, which pushed the h-screen shell (and Footer) mid-viewport
  // during an active run. scrollTo on the chat container only moves ChatArea.
  const rafRef = useRef(0)

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    stickToBottomRef.current = distance < 80
  }

  useLayoutEffect(() => {
    if (!stickToBottomRef.current) return
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
    })
    return () => cancelAnimationFrame(rafRef.current)
  }, [messages, activeRun])

  useEffect(() => {
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="m-1 min-h-0 space-y-6 flex-1 overflow-y-auto overscroll-contain px-8 py-10 bg-white border border-gray-400 rounded-xl shadow-sm"
    >
      {messages.map((message) => {
        // The in-flight run renders its plan + artifacts inside the run's
        // placeholder bubble; finished messages render from their rows.
        // Completed runs keep a collapsed-all Steps snapshot (pastRuns) so
        // the trace stays inspectable after activeRun is cleared; the main
        // bubble always shows the SHOW-only summary text.
        const runView =
          activeRun && activeRun.messageId === message.id
            ? activeRun
            : (pastRuns?.[message.id] ?? null)
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
          // Thinking gap: streaming placeholder with no tokens yet shows
          // the 3-dot indicator; it hides on the first delta/summary.
          const isThinking =
            message.status === 'streaming' && !message.text?.trim()
          if (isThinking) {
            return (
              <AssistantMessage
                key={message.id}
                plan={runView?.plan ?? null}
                goal={runView?.goal ?? null}
                artifacts={artifacts}
                stepResults={runView?.stepResults}
              >
                <TypingDots />
              </AssistantMessage>
            )
          }
          return (
            <AssistantMessage
              key={message.id}
              text={message.text}
              sources={message.sources}
              plan={runView?.plan ?? null}
              goal={runView?.goal ?? null}
              artifacts={artifacts}
              stepResults={runView?.stepResults}
            />
          )
        }
        if (message.role === 'user') {
          return <UserMessage key={message.id} text={message.text} />
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
  stepResults?: Record<string, {executor:string,status:string,output:string,delta:string,expected_output_type?:string,visibility?:'show'|'hide'}>
}

function AssistantMessage({
  text,
  children,
  sources = [],
  plan = null,
  goal = null,
  artifacts = [],
  stepResults = {},
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
        <div className="text-sm text-gray-600 leading-relaxed max-w-2xl border border-gray-200 p-4 rounded-xl rounded-tl-none bg-gray-100">
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

          {plan && plan.length > 0 && stepResults && Object.keys(stepResults).length > 0 && (
            <details className="mt-3 text-[11px] text-gray-500">
              <summary className="cursor-pointer font-semibold hover:text-gray-700">
                Steps (all, collapsed)
              </summary>
              <div className="mt-1 ml-2 space-y-1">
                {plan.map((step) => {
                  const sr = stepResults[step.step_id]
                  if (!sr) return null
                  const raw = sr.status === 'running' ? sr.delta || '' : sr.output || sr.delta || ''
                  // Never dump raw chart SVG into the panel (same guard as
                  // the main bubble): the chart renders via Artifacts <img>.
                  const content = stripSvgFromText(raw) || ''
                  const vis = sr.visibility ?? step.visibility ?? null
                  const eot = sr.expected_output_type ?? step.expected_output_type ?? null
                  const badge = vis === 'hide' ? 'hidden' : vis === 'show' ? 'shown' : null
                  return (
                    <details key={step.step_id} className="border-l-2 border-gray-300 pl-2">
                      <summary className="cursor-pointer font-semibold hover:text-gray-700">
                        Step {step.step_id} · {step.executor} · {sr.status}
                        {eot ? ` · ${eot}` : ''}
                        {badge ? ` · ${badge}` : ''}
                      </summary>
                      <div className="max-h-48 overflow-y-auto overscroll-contain whitespace-pre-wrap break-words text-[10px] text-gray-600">
                        {content}
                      </div>
                    </details>
                  )
                })}
              </div>
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
