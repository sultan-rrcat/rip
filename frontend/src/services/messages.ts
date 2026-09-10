import { API } from '@/config'
import type { Message, MessageRole, Source } from '@/types'

export async function getMessagesAPI(
  notebookId: string,
): Promise<Message[]> {
  console.log(`[messages] GET /api/notebooks/${notebookId}/messages`)
  const res = await fetch(`${API}/api/notebooks/${notebookId}/messages`)
  const data: Message[] = await res.json()
  console.log(`[messages] GET /api/notebooks/${notebookId}/messages →`, data)
  return data
}

export async function createMessageAPI(
  notebookId: string,
  role: MessageRole,
  text: string,
  sources?: Source[],
): Promise<Message> {
  console.log(`[messages] POST /api/notebooks/${notebookId}/messages`, {
    role,
    text,
    sources,
  })
  const res = await fetch(`${API}/api/notebooks/${notebookId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, text, sources }),
  })
  const data: Message = await res.json()
  console.log(`[messages] POST /api/notebooks/${notebookId}/messages →`, data)
  return data
}
