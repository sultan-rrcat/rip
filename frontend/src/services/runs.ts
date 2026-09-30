import { API } from '@/config'
import { request, jsonInit } from '@/services/http'
import type { RunEvent } from '@/types/runs'

export async function createRun(
  notebookId: string,
  message: string,
): Promise<string> {
  const data = await request<{ run_id: string }>(
    '/v1/runs',
    jsonInit('POST', { notebook_id: notebookId, message }),
  )
  return data.run_id
}

const MAX_RETRIES = 5
const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000]

export function subscribeToRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError?: () => void,
): () => void {
  let source: EventSource | null = null
  let retryCount = 0
  let closed = false
  let timer: ReturnType<typeof setTimeout> | null = null

  const connect = () => {
    if (closed) return
    source = new EventSource(`${API}/v1/runs/${runId}/events`)

    source.onopen = () => {
      retryCount = 0
    }

    source.onmessage = (msg: MessageEvent) => {
      try {
        onEvent(JSON.parse(msg.data) as RunEvent)
      } catch {
        // A malformed frame is not fatal: skip it and keep the stream open.
      }
    }

    source.onerror = () => {
      source?.close()
      if (closed) return
      if (retryCount >= MAX_RETRIES) {
        onError?.()
        return
      }
      const delay = BACKOFF_MS[retryCount] ?? BACKOFF_MS[BACKOFF_MS.length - 1]
      retryCount++
      timer = setTimeout(connect, delay)
    }
  }

  connect()

  return () => {
    closed = true
    if (timer) clearTimeout(timer)
    source?.close()
  }
}

export async function cancelRun(runId: string): Promise<void> {
  const response = await fetch(`${API}/v1/runs/${runId}/cancel`, {
    method: 'POST',
    credentials: 'include',
  })

  if (!response.ok) {
    throw new Error(`Server responded with ${response.status}`)
  }
}
