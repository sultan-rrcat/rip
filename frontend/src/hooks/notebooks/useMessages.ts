import { useState, useEffect, useCallback, useRef } from 'react'
import { v4 as uuidv4 } from 'uuid'
import { getMessagesAPI, createMessageAPI } from '@/services/messages'
import { createRun, subscribeToRunEvents, cancelRun } from '@/services/runs'
import type { Message, MessageArtifact, Source } from '@/types'
import type { RunEvent, RunView, PlanStep } from '@/types/runs'

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
// types/runs.ts header): step_started carries executor_id, error carries
// message. Read once at the boundary, with fallbacks — never spread.
type WireEvent = RunEvent & Record<string, unknown>

function eventText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function useMessages(notebook_id: string | undefined) {
  const [messages, setMessages] = useState<Message[]>(DEFAULT_MESSAGES)
  const [activeRun, setActiveRun] = useState<RunView | null>(null)
  const [isRunning, setIsRunning] = useState(false)

  // Mutable run mirrors (refs avoid stale closures in the SSE callback):
  // accumulated text, sources, unsubscribe, dedupe set, placeholder id.
  const textRef = useRef('')
  const sourcesRef = useRef<Source[]>([])
  const artifactsRef = useRef<MessageArtifact[]>([])
  const unsubscribeRef = useRef<(() => void) | null>(null)
  const seenRef = useRef<Set<string>>(new Set())
  const placeholderRef = useRef<string | null>(null)
  // Step results for collapsible steps view
  const stepResultsRef = useRef<Record<string, {executor:string,status:string,output:string,delta:string}>>({})
  const planStepsRef = useRef<PlanStep[] | null>(null)
  // Notebook owning the current subscription. Written in effects/handlers
  // only (never during render) so the SSE callbacks can't go stale.
  const nbRef = useRef<string | undefined>(undefined)

  const detach = useCallback(() => {
    unsubscribeRef.current?.()
    unsubscribeRef.current = null
    placeholderRef.current = null
    setActiveRun(null)
    setIsRunning(false)
  }, [])

  const finalizeCompleted = useCallback(
    async (runId: string) => {
      const nb = nbRef.current
      const messageId = placeholderRef.current
      detach()
      if (!nb || !messageId) return
      // Exactly-once assistant persist: a resumed replay of an already
      // saved run only removes the placeholder (the saved row is loaded).
      if (localStorage.getItem(persistedKey(nb)) === runId) {
        localStorage.removeItem(activeKey(nb))
        setMessages((prev) => prev.filter((m) => m.id !== messageId))
        return
      }
      const saved = await createMessageAPI(
        nb,
        'assistant',
        textRef.current,
        sourcesRef.current,
        artifactsRef.current,
      )
      localStorage.setItem(persistedKey(nb), runId)
      localStorage.removeItem(activeKey(nb))
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId ? { ...saved, status: 'done' as const } : m,
        ),
      )
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
      const errorMessage = await createMessageAPI(nb, 'error', text)
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? errorMessage : m)),
      )
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
          m.id === messageId
            ? { ...m, status: 'done' as const }
            : m,
        ),
      )
    }
  }, [detach])

  const handleEvent = useCallback(
    (runId: string, event: RunEvent) => {
      const evt = event as WireEvent
      // Dedupe by seq across replay + live (Q35): String() covers the
      // fractional live-only delta seqs ("3.1").
      const key = String(evt.seq)
      if (seenRef.current.has(key)) return
      seenRef.current.add(key)
      const messageId = placeholderRef.current
      if (!messageId) return

      switch (evt.type) {
        case 'plan': {
          const planEvt = evt as Extract<RunEvent, { type: 'plan' }>
          planStepsRef.current = planEvt.steps
          // initialise step results map
          const initSteps: Record<string, {executor:string,status:string,output:string,delta:string}> = {}
          for (const s of planEvt.steps) {
            initSteps[s.step_id] = { executor: s.executor, status: 'pending', output: '', delta: '' }
          }
          stepResultsRef.current = initSteps
          setActiveRun((r) =>
            r && r.runId === runId
              ? { ...r, goal: planEvt.goal, plan: planEvt.steps, stepResults: initSteps }
              : r,
          )
          break
        }
        case 'delta': {
          const text = eventText(evt.content)
          if (!text) break
          const stepId = (evt as any).step_id as string | undefined
          const planSteps = planStepsRef.current
          const finalStepId = planSteps && planSteps.length > 0 ? planSteps[planSteps.length - 1].step_id : undefined
          const isFinal = stepId && finalStepId && stepId === finalStepId
          if (isFinal) {
            textRef.current += text
            setMessages((prev) =>
              prev.map((m) =>
                m.id === messageId
                  ? { ...m, text: textRef.current, status: 'streaming' as const }
                  : m,
              ),
            )
          } else if (stepId) {
            // stream into step panel
            const step = stepResultsRef.current[stepId]
            if (step) {
              step.delta += text
              stepResultsRef.current[stepId] = { ...step, delta: step.delta }
              setActiveRun(r => r && r.runId === runId ? { ...r, stepResults: { ...stepResultsRef.current } } : r)
            }
          }
          break
        }
        case 'summary': {
          // Authoritative full text — replaces token accumulation so a
          // resumed replay (no deltas, Q35) still renders the answer.
          const summaryEvt = evt as Extract<RunEvent, { type: 'summary' }>
          textRef.current = summaryEvt.content
          setMessages((prev) =>
            prev.map((m) =>
              m.id === messageId
                ? { ...m, text: summaryEvt.content, status: 'streaming' as const }
                : m,
            ),
          )
          break
        }
        case 'sources': {
          const sourcesEvt = evt as Extract<RunEvent, { type: 'sources' }>
          sourcesRef.current = [...sourcesRef.current, ...sourcesEvt.sources]
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
          const artEvt = evt as Extract<RunEvent, { type: 'artifacts' }>
          artifactsRef.current = [...artifactsRef.current, ...artEvt.artifacts]
          const accumulated = artifactsRef.current
          setActiveRun((r) =>
            r && r.runId === runId
              ? { ...r, artifacts: [...r.artifacts, ...artEvt.artifacts] }
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
        case 'error':
          void finalizeError(
            `Something went wrong. Error: ${eventText(evt.error) || eventText(evt.message) || 'unknown'}`,
          )
          break
        case 'step_started': {
          const sEvt = evt as any
          const stepId = sEvt.step_id as string
          if (stepId && stepResultsRef.current[stepId]) {
            stepResultsRef.current[stepId] = { ...stepResultsRef.current[stepId], status: 'running' }
            setActiveRun(r => r && r.runId === runId ? { ...r, stepResults: { ...stepResultsRef.current } } : r)
          }
          break
        }
        case 'step_completed': {
          const sEvt = evt as any
          const stepId = sEvt.step_id as string
          if (stepId && stepResultsRef.current[stepId]) {
            stepResultsRef.current[stepId] = {
              ...stepResultsRef.current[stepId],
              status: sEvt.status || 'done',
              output: sEvt.output || stepResultsRef.current[stepId].delta
            }
            setActiveRun(r => r && r.runId === runId ? { ...r, stepResults: { ...stepResultsRef.current } } : r)
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
      const unsubscribe = subscribeToRunEvents(runId, (event) =>
        handleEvent(runId, event),
      )
      unsubscribeRef.current = unsubscribe
    },
    [handleEvent],
  )

  // Load persisted messages on mount; resume an in-flight run if the page
  // was refreshed mid-run (replay persisted structural events, Q35).
  useEffect(() => {
    if (!notebook_id) return
    const id = notebook_id
    nbRef.current = id
    let cancelled = false

    async function load() {
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
      // 1. Persist the user message (frontend owns messages, Q22)
      const userMessage = await createMessageAPI(notebook_id, 'user', text)

      // 2. Optimistic user message + streaming placeholder
      const assistantTempId = uuidv4()
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

      try {
        // 3. Create the run, then stream its events (plan collapsible,
        // deltas/summary as tokens, sources/artifacts accumulated)
        const runId = await createRun(notebook_id, text)
        attach(runId, assistantTempId, '')
      } catch (err) {
        const errorMessage = await createMessageAPI(
          notebook_id,
          'error',
          `Something went wrong. Error: ${err}`,
        )
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantTempId ? errorMessage : m)),
        )
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
    isRunning,
    handleSendMessage,
    handleCancelRun,
  }
}
