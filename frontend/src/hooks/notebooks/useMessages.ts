import { useState, useEffect, useCallback, useRef } from 'react'
import { v4 as uuidv4 } from 'uuid'
import { getMessagesAPI, createMessageAPI } from '@/services/messages'
import { createRun, subscribeToRunEvents, cancelRun } from '@/services/runs'
import type { Artifact, Message, Source } from '@/types'
import type { RunEvent, RunView, PlanStep, StepResultView } from '@/types/runs'

const DEFAULT_MESSAGES: Message[] = [
  {
    id: uuidv4(),
    role: 'assistant',
    text: 'How can I help you?',
    sources: [],
  },
]

const activeKey = (notebookId: string) => `rip:activeRun:${notebookId}`
const persistedKey = (notebookId: string) => `rip:persistedRun:${notebookId}`

// Backend payload extras outside the locked RunEvent union (see
// types/runs.ts header): plan carries `attempt`, error carries `message`.
// Read once at the boundary, with fallbacks — never spread.
type WirePlanEvent = Extract<RunEvent, { type: 'plan' }> & { attempt?: number }
type WireErrorEvent = Extract<RunEvent, { type: 'error' }> & {
  message?: string
}

function eventText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function useMessages(notebook_id: string | undefined) {
  const [messages, setMessages] = useState<Message[]>(DEFAULT_MESSAGES)
  const [activeRun, setActiveRun] = useState<RunView | null>(null)
  const [isRunning, setIsRunning] = useState(false)

  // Mutable run mirrors (refs avoid stale closures in the SSE callback):
  // accumulated text, sources, artifacts, unsubscribe, dedupe set, placeholder id.
  const textRef = useRef('')
  const sourcesRef = useRef<Source[]>([])
  const artifactsRef = useRef<Artifact[]>([])
  const unsubscribeRef = useRef<(() => void) | null>(null)
  const seenRef = useRef<Set<string>>(new Set())
  const placeholderRef = useRef<string | null>(null)
  // Step results for collapsible steps view
  const stepResultsRef = useRef<Record<string, StepResultView>>({})
  const planStepsRef = useRef<PlanStep[] | null>(null)
  const goalRef = useRef<string | null>(null)
  // Completed runs retained per assistant message so the collapsed-all
  // Steps panel + SHOW-only answer persist after run_completed (activeRun
  // is cleared) and across replay.
  const [pastRuns, setPastRuns] = useState<Record<string, RunView>>({})
  // Notebook owning the current subscription. Written in effects/handlers
  // only (never during render) so the SSE callbacks can't go stale.
  const nbRef = useRef<string | undefined>(undefined)

  const detach = useCallback(() => {
    unsubscribeRef.current?.()
    unsubscribeRef.current = null
    placeholderRef.current = null
    stepResultsRef.current = {}
    planStepsRef.current = null
    goalRef.current = null
    setActiveRun(null)
    setIsRunning(false)
  }, [])

  const finalizeCompleted = useCallback(
    async (runId: string) => {
      const nb = nbRef.current
      const messageId = placeholderRef.current
      // Snapshot the collapsed-all Steps panel before detach clears it.
      if (messageId && planStepsRef.current) {
        const snapshot: RunView = {
          runId,
          messageId,
          goal: goalRef.current,
          plan: planStepsRef.current,
          sources: sourcesRef.current,
          artifacts: artifactsRef.current,
          running: false,
          stepResults: { ...stepResultsRef.current },
        }
        setPastRuns((prev) => ({ ...prev, [messageId]: snapshot }))
      }
      detach()
      if (!nb || !messageId) return
      // Exactly-once assistant persist: a resumed replay of an already
      // saved run only removes the placeholder (the saved row is loaded).
      if (localStorage.getItem(persistedKey(nb)) === runId) {
        localStorage.removeItem(activeKey(nb))
        setMessages((prev) => prev.filter((m) => m.id !== messageId))
        return
      }
      try {
        const saved = await createMessageAPI(
          nb,
          'assistant',
          textRef.current,
          sourcesRef.current,
          artifactsRef.current,
        )
        localStorage.setItem(persistedKey(nb), runId)
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId ? { ...saved, status: 'done' as const } : m,
          ),
        )
      } catch {
        // Persist failed: keep the accumulated answer on screen rather than
        // leaving the placeholder stuck in a streaming state.
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId ? { ...m, status: 'done' as const } : m,
          ),
        )
      } finally {
        localStorage.removeItem(activeKey(nb))
      }
    },
    [detach],
  )

  const finalizeError = useCallback(
    async (text: string) => {
      const nb = nbRef.current
      const messageId = placeholderRef.current
      detach()
      if (!nb || !messageId) return
      localStorage.removeItem(activeKey(nb))
      try {
        const errorMessage = await createMessageAPI(nb, 'error', text)
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? errorMessage : m)),
        )
      } catch {
        // Persisting the error failed too: show it locally.
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, role: 'error' as const, text, status: 'done' as const }
              : m,
          ),
        )
      }
    },
    [detach],
  )

  const finalizeCancelled = useCallback(() => {
    const nb = nbRef.current
    const messageId = placeholderRef.current
    detach()
    if (nb) localStorage.removeItem(activeKey(nb))
    // Partial tokens stay on screen unpersisted (no run_completed → the
    // frontend never writes an assistant row for a cancelled run, Q22).
    if (messageId) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId ? { ...m, status: 'done' as const } : m,
        ),
      )
    }
  }, [detach])

  const handleEvent = useCallback(
    (runId: string, event: RunEvent) => {
      // Dedupe by seq across replay + live (Q35): String() covers the
      // fractional live-only delta seqs ("3.1").
      const key = String(event.seq)
      if (seenRef.current.has(key)) return
      seenRef.current.add(key)
      const messageId = placeholderRef.current
      if (!messageId) return

      switch (event.type) {
        case 'plan': {
          const planEvt = event as WirePlanEvent
          // Planner recall: a second plan for the same run means attempt 1
          // failed. Its tokens/sources/artifacts belong to the dead attempt
          // and must not pollute the retry — reset every accumulator.
          // Detected via the additive `attempt` field, with prior-plan
          // presence as fallback (replay-safe: attach() clears the ref).
          const attempt = planEvt.attempt ?? 1
          if (attempt > 1 || planStepsRef.current !== null) {
            textRef.current = ''
            sourcesRef.current = []
            artifactsRef.current = []
            setMessages((prev) =>
              prev.map((m) =>
                m.id === messageId
                  ? { ...m, text: '', sources: [], artifacts: [] }
                  : m,
              ),
            )
          }
          planStepsRef.current = planEvt.steps
          goalRef.current = planEvt.goal ?? null
          // initialise step results map (carry visibility/eot when present;
          // older replays omit them and fall back to show/unknown)
          const initSteps: Record<string, StepResultView> = {}
          for (const s of planEvt.steps) {
            initSteps[s.step_id] = {
              executor: s.executor,
              status: 'pending',
              output: '',
              delta: '',
              expected_output_type: s.expected_output_type,
              visibility: s.visibility,
            }
          }
          stepResultsRef.current = initSteps
          setActiveRun((r) =>
            r && r.runId === runId
              ? {
                  ...r,
                  goal: planEvt.goal,
                  plan: planEvt.steps,
                  sources: [],
                  artifacts: [],
                  stepResults: initSteps,
                }
              : r,
          )
          break
        }
        case 'delta': {
          const text = eventText(event.content)
          if (!text) break
          // Stream every delta into the main bubble (matches the
          // concatenated summary); mirror into the Steps panel when the
          // step is known. Never drop: deltas arriving before `plan` or
          // without a step_id still belong to the answer.
          textRef.current += text
          setMessages((prev) =>
            prev.map((m) =>
              m.id === messageId
                ? { ...m, text: textRef.current, status: 'streaming' as const }
                : m,
            ),
          )
          const stepId = event.step_id
          if (stepId) {
            const step = stepResultsRef.current[stepId]
            if (step) {
              const updated = { ...step, delta: step.delta + text }
              stepResultsRef.current[stepId] = updated
              setActiveRun((r) =>
                r && r.runId === runId
                  ? { ...r, stepResults: { ...stepResultsRef.current } }
                  : r,
              )
            }
          }
          break
        }
        case 'summary': {
          // Authoritative full text — replaces token accumulation so a
          // resumed replay (no deltas, Q35) still renders the answer.
          textRef.current = event.content
          setMessages((prev) =>
            prev.map((m) =>
              m.id === messageId
                ? { ...m, text: event.content, status: 'streaming' as const }
                : m,
            ),
          )
          break
        }
        case 'sources': {
          sourcesRef.current = [...sourcesRef.current, ...event.sources]
          const accumulated = sourcesRef.current
          setActiveRun((r) =>
            r && r.runId === runId ? { ...r, sources: accumulated } : r,
          )
          setMessages((prev) =>
            prev.map((m) =>
              m.id === messageId ? { ...m, sources: accumulated } : m,
            ),
          )
          break
        }
        case 'artifacts': {
          artifactsRef.current = [...artifactsRef.current, ...event.artifacts]
          const accumulated = artifactsRef.current
          setActiveRun((r) =>
            r && r.runId === runId
              ? { ...r, artifacts: [...r.artifacts, ...event.artifacts] }
              : r,
          )
          setMessages((prev) =>
            prev.map((m) =>
              m.id === messageId ? { ...m, artifacts: accumulated } : m,
            ),
          )
          break
        }
        case 'run_completed':
          void finalizeCompleted(runId)
          break
        case 'error': {
          const errorEvt = event as WireErrorEvent
          void finalizeError(
            `Something went wrong. Error: ${eventText(errorEvt.error) || eventText(errorEvt.message) || 'unknown'}`,
          )
          break
        }
        case 'step_started': {
          const stepId = event.step_id
          if (stepId && stepResultsRef.current[stepId]) {
            const startedEvt = event as RunEvent & {
              expected_output_type?: string
              visibility?: 'show' | 'hide'
            }
            stepResultsRef.current[stepId] = {
              ...stepResultsRef.current[stepId],
              status: 'running',
              ...(startedEvt.expected_output_type !== undefined
                ? { expected_output_type: startedEvt.expected_output_type }
                : {}),
              ...(startedEvt.visibility !== undefined
                ? { visibility: startedEvt.visibility }
                : {}),
            }
            setActiveRun((r) =>
              r && r.runId === runId
                ? { ...r, stepResults: { ...stepResultsRef.current } }
                : r,
            )
          }
          break
        }
        case 'step_completed': {
          const stepId = event.step_id
          if (stepId && stepResultsRef.current[stepId]) {
            const completedEvt = event as RunEvent & {
              expected_output_type?: string
              visibility?: 'show' | 'hide'
            }
            stepResultsRef.current[stepId] = {
              ...stepResultsRef.current[stepId],
              status: event.status || 'done',
              output: event.output || stepResultsRef.current[stepId].delta,
              ...(completedEvt.expected_output_type !== undefined
                ? { expected_output_type: completedEvt.expected_output_type }
                : {}),
              ...(completedEvt.visibility !== undefined
                ? { visibility: completedEvt.visibility }
                : {}),
            }
            setActiveRun((r) =>
              r && r.runId === runId
                ? { ...r, stepResults: { ...stepResultsRef.current } }
                : r,
            )
          }
          break
        }
        case 'cancelled':
          finalizeCancelled()
          break
        default:
          break // run_started: no UI state
      }
    },
    [finalizeCompleted, finalizeError, finalizeCancelled],
  )

  const attach = useCallback(
    (runId: string, messageId: string, initialText: string) => {
      const nb = nbRef.current
      if (!nb) return
      seenRef.current = new Set()
      textRef.current = initialText
      sourcesRef.current = []
      artifactsRef.current = []
      placeholderRef.current = messageId
      stepResultsRef.current = {}
      planStepsRef.current = null
      localStorage.setItem(activeKey(nb), runId)
      setActiveRun({
        runId,
        messageId,
        goal: null,
        plan: null,
        sources: [],
        artifacts: [],
        running: true,
        stepResults: {},
      })
      setIsRunning(true)
      unsubscribeRef.current?.()
      const unsubscribe = subscribeToRunEvents(
        runId,
        (event) => handleEvent(runId, event),
        () => {
          // The stream dropped before a terminal event: surface it instead
          // of leaving the UI stuck in a running state.
          void finalizeError('Connection to the run was lost.')
        },
      )
      unsubscribeRef.current = unsubscribe
    },
    [handleEvent, finalizeError],
  )

  // Load persisted messages on mount; resume an in-flight run if the page
  // was refreshed mid-run (replay persisted structural events, Q35).
  useEffect(() => {
    if (!notebook_id) return
    const id = notebook_id
    nbRef.current = id
    let cancelled = false

    async function load() {
      setPastRuns({})
      try {
        const msgs = await getMessagesAPI(id)
        if (cancelled) return
        setMessages(msgs.length > 0 ? msgs : DEFAULT_MESSAGES)
      } catch (err) {
        console.error('Failed to load messages:', err)
      }
      if (cancelled) return
      const pendingRun = localStorage.getItem(activeKey(id))
      if (!pendingRun || placeholderRef.current) return
      const messageId = uuidv4()
      setMessages((prev) => [
        ...prev,
        {
          id: messageId,
          role: 'assistant',
          text: '',
          status: 'streaming',
          sources: [],
        },
      ])
      attach(pendingRun, messageId, '')
    }

    load()
    return () => {
      cancelled = true
      unsubscribeRef.current?.()
      unsubscribeRef.current = null
      // Full reset so a StrictMode remount (or notebook switch) re-attaches
      // from scratch instead of skipping on a stale placeholder.
      placeholderRef.current = null
      seenRef.current = new Set()
      setActiveRun(null)
      setIsRunning(false)
    }
  }, [notebook_id, attach])

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!notebook_id || isRunning) return
      nbRef.current = notebook_id
      const assistantTempId = uuidv4()
      let placeholderAdded = false

      try {
        // 1. Persist the user message (frontend owns messages, Q22)
        const userMessage = await createMessageAPI(notebook_id, 'user', text)

        // 2. Optimistic user message + streaming placeholder
        setMessages((prev) => [
          ...prev,
          userMessage,
          {
            id: assistantTempId,
            role: 'assistant',
            text: '',
            status: 'streaming',
            sources: [],
          },
        ])
        placeholderAdded = true

        // 3. Create the run, then stream its events (plan collapsible,
        // deltas/summary as tokens, sources/artifacts accumulated)
        const runId = await createRun(notebook_id, text)
        attach(runId, assistantTempId, '')
      } catch (err) {
        const errorText = `Something went wrong. Error: ${err}`
        try {
          const errorMessage = await createMessageAPI(
            notebook_id,
            'error',
            errorText,
          )
          setMessages((prev) =>
            placeholderAdded
              ? prev.map((m) =>
                  m.id === assistantTempId ? errorMessage : m,
                )
              : [...prev, errorMessage],
          )
        } catch {
          // Persisting the error failed too: surface it locally.
          setMessages((prev) =>
            placeholderAdded
              ? prev.map((m) =>
                  m.id === assistantTempId
                    ? {
                        ...m,
                        role: 'error' as const,
                        text: errorText,
                        status: 'done' as const,
                      }
                    : m,
                )
              : [
                  ...prev,
                  {
                    id: assistantTempId,
                    role: 'error' as const,
                    text: errorText,
                    sources: [],
                    status: 'done' as const,
                  },
                ],
          )
        }
      }
    },
    [notebook_id, isRunning, attach],
  )

  const handleCancelRun = useCallback(async () => {
    const runId = activeRun?.runId
    if (!runId) return
    try {
      await cancelRun(runId)
    } catch (err) {
      console.error('Failed to cancel run:', err)
    }
    // The worker's `cancelled` event finalizes the UI; the subscription
    // stays open for it.
  }, [activeRun])

  return {
    messages,
    activeRun,
    pastRuns,
    isRunning,
    handleSendMessage,
    handleCancelRun,
  }
}
