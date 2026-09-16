import { API } from '@/config'
import { request, jsonInit } from '@/services/http'
import type { RunEvent } from '@/types/runs'

// Run lifecycle client (MERGE_PLAN.md §Frontend, Q1–Q3 locked):
//   createRun → POST /v1/runs {notebook_id, message} → 202 {run_id}
//   subscribeToRunEvents → EventSource GET /v1/runs/{id}/events
//   cancelRun → POST /v1/runs/{id}/cancel
// Bare JSON on /v1/* (no BFF envelope). No hardcoded host — API comes from
// VITE_API_URL via @/config. Reconnect/dedupe policy lives in useMessages;
// this module only opens the stream and parses frames.

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

export function subscribeToRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(`${API}/v1/runs/${runId}/events`)

  source.onmessage = (msg: MessageEvent) => {
    try {
      onEvent(JSON.parse(msg.data) as RunEvent)
    } catch {
      // A malformed frame is not fatal: skip it and keep the stream open.
    }
  }

  // A closed/errored stream is terminal for this subscription (run finished
  // or gone). Notify the caller so it can surface the loss instead of
  // leaving the UI stuck "running"; no auto-reconnect here.
  source.onerror = () => {
    source.close()
    onError?.()
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
