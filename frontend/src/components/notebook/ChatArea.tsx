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
      <h1 className="font-display mt-4 mb-2 text-xl font-bold leading-tight text-ink">
        {children}
      </h1>
    )
  },
  h2({ children }) {
    return (
      <h2 className="font-display mt-4 mb-2 text-lg font-bold leading-tight text-ink">
        {children}
      </h2>
    )
  },
  h3({ children }) {
    return (
      <h3 className="mt-3 mb-1.5 text-base font-semibold leading-snug text-ink">
        {children}
      </h3>
    )
  },
  h4({ children }) {
    return (
      <h4 className="mt-3 mb-1 text-sm font-semibold leading-snug text-ink">
        {children}
      </h4>
    )
  },
  h5({ children }) {
    return (
      <h5 className="mt-2 mb-1 text-sm font-semibold text-ink-soft">
        {children}
      </h5>
    )
  },
  h6({ children }) {
    return (
      <h6 className="font-ledger mt-2 mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-soft/80">
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
        className="font-medium text-ledger underline underline-offset-2 hover:text-ink"
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
      <blockquote className="my-2 border-l-[3px] border-brass pl-3 italic text-ink-soft">
        {children}
      </blockquote>
    )
  },
  hr() {
    return <hr className="my-4 border-line" />
  },
  strong({ children }) {
    return (
      <strong className="font-semibold text-ink">{children}</strong>
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
        className={`rounded bg-ink/[0.06] px-1 py-0.5 font-mono text-[0.85em] text-ink ${className ?? ''}`}
      >
        {children}
      </code>
    )
  },
  // Ensure tables look good with Tailwind
  table({ children }) {
    return (
      <div className="overflow-x-auto my-2">
        <table className="border-collapse border border-line min-w-full">
          {children}
        </table>
      </div>
    )
  },
  th({ children }) {
    return (
      <th className="font-ledger border border-line px-4 py-2 bg-paper text-[11px] tracking-wide text-ink">
        {children}
      </th>
    )
  },
  td({ children }) {
    return <td className="border border-line px-4 py-2">{children}</td>
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
      className="m-0 min-h-0 space-y-6 flex-1 overflow-y-auto overscroll-contain px-5 py-8 sm:px-8 bg-paper border border-line rounded-lg shadow-[0_1px_0_rgba(21,39,54,0.12)]"
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
      <div className="w-8 h-8 rounded-full bg-ink text-paper flex items-center justify-center shrink-0">
        <SmartToyIcon fontSize="small" />
      </div>
      {/* content */}
      <div className="flex-1 min-w-0 space-y-1">
        <p className="font-ledger text-[10px] font-semibold tracking-[0.18em] text-ink-soft/60">ASSISTANT</p>
        <div className="text-sm text-ink/90 leading-relaxed max-w-2xl border border-line p-4 rounded-lg rounded-tl-sm bg-card shadow-[0_1px_0_rgba(21,39,54,0.1)]">
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
            <details className="font-ledger mt-3 text-[11px] text-ink-soft/80">
              <summary className="cursor-pointer font-semibold hover:text-ink">
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
            <details className="font-ledger mt-3 text-[11px] text-ink-soft/80">
              <summary className="cursor-pointer font-semibold hover:text-ink">
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
                    <details key={step.step_id} className="border-l-2 border-brass/60 pl-2">
                      <summary className="cursor-pointer font-semibold hover:text-ink">
                        Step {step.step_id} · {step.executor} · {sr.status}
                        {eot ? ` · ${eot}` : ''}
                        {badge ? ` · ${badge}` : ''}
                      </summary>
                      <div className="max-h-48 overflow-y-auto overscroll-contain whitespace-pre-wrap break-words text-[10px] text-ink-soft">
                        {content}
                      </div>
                    </details>
                  )
                })}
              </div>
            </details>
          )}

          {artifacts && artifacts.length > 0 && (
            <div className="mt-3 pt-2 border-t border-line">
              <p className="font-ledger text-[10px] font-semibold tracking-[0.18em] text-ink-soft/60 mb-1">
                ARTIFACTS
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
            <div className="mt-4 pt-3 border-t border-line">
              <p className="font-ledger text-[10px] font-semibold tracking-[0.18em] text-ink-soft/60 mb-2">
                SOURCES
              </p>
              <ul className="flex flex-wrap gap-1.5">
                {sources.length > 0 &&
                  sources.map((s, i) => (
                    <li key={i} className="font-ledger inline-flex max-w-full items-center gap-1.5 rounded border border-line bg-paper px-2 py-1 text-[10px] text-ink-soft">
                      <span className="font-semibold text-brass-deep">[{i + 1}]</span>
                      <span className="truncate">{s.source} — {s.section}</span>
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
        <p className="font-ledger text-[10px] font-semibold tracking-[0.18em] text-ink-soft/60">YOU</p>
        <div className="inline-block text-paper bg-ink px-5 py-2.5 rounded-lg rounded-tr-sm text-[13px] leading-relaxed shadow-sm max-w-2xl text-left">
          {text}
        </div>
      </div>
    </div>
  )
}

function ErrorMessage({ text }: TextMessageProps) {
  return (
    <div className="flex gap-4 items-start">
      <div className="w-8 h-8 rounded-full bg-rust/15 text-rust flex items-center justify-center shrink-0">
        <SmartToyIcon fontSize="small" />
      </div>
      <div className="flex-1 space-y-1">
        <p className="font-ledger text-[10px] font-semibold tracking-[0.18em] text-rust">ASSISTANT · ERROR</p>
        <div className="text-sm text-rust leading-relaxed max-w-2xl">
          {text}
        </div>
      </div>
    </div>
  )
}
