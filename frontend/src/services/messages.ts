import { request, jsonInit } from '@/services/http'
import type { Artifact, Message, MessageRole, Source } from '@/types'

export function getMessagesAPI(notebookId: string): Promise<Message[]> {
  return request<Message[]>(`/api/notebooks/${notebookId}/messages`)
}

export function createMessageAPI(
  notebookId: string,
  role: MessageRole,
  text: string,
  sources?: Source[],
  artifacts?: Artifact[],
): Promise<Message> {
  return request<Message>(
    `/api/notebooks/${notebookId}/messages`,
    jsonInit('POST', { role, text, sources, artifacts }),
  )
}

export function updateMessageAPI(
  notebookId: string,
  messageId: string,
  text: string,
): Promise<Message> {
  return request<Message>(
    `/api/notebooks/${notebookId}/messages/${messageId}`,
    jsonInit('PUT', { text }),
  )
}
