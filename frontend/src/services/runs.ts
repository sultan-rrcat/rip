import { API } from '@/config'
import type { RunEvent } from '@/types/runs'

// Run lifecycle client (MERGE_PLAN.md §Frontend, Q1–Q3 locked):
//   createRun → POST /v1/runs {notebook_id, message} → 202 {run_id}
//   subscribeToRunEvents → EventSource GET /v1/runs/{id}/events
//   cancelRun → POST /v1/runs/{id}/cancel
// Bare JSON on /v1/* (no BFF envelope). No hardcoded host — API comes from
// VITE_API_URL via @/config. Reconnect/dedupe policy lives in 5.3
// (useMessages); this module only opens the stream and parses frames.

export async function createRun(
  notebookId: string,
  message: string,
): Promise<string> {
  const response = await fetch(`${API}/v1/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notebook_id: notebookId, message }),
  })

  if (!response.ok) {
    throw new Error(`Server responded with ${response.status}`)
  }
  const data = await response.json()
  return data.run_id as string
}

export function subscribeToRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
): () => void {
  const source = new EventSource(`${API}/v1/runs/${runId}/events`)

  source.onmessage = (msg: MessageEvent) => {
    try {
      onEvent(JSON.parse(msg.data) as RunEvent)
    } catch (err) {
      console.error('Run event parse error:', err)
    }
  }

  // No auto-reconnect here: a closed/errored stream is terminal for this
  // subscription (run finished or gone). The caller re-subscribes
  // deliberately (e.g. refresh → replay persisted events, Q35).
  source.onerror = () => {
    source.close()
  }

  return () => source.close()
}

export async function cancelRun(runId: string): Promise<void> {
  const response = await fetch(`${API}/v1/runs/${runId}/cancel`, {
    method: 'POST',
  })

  if (!response.ok) {
    throw new Error(`Server responded with ${response.status}`)
  }
}
