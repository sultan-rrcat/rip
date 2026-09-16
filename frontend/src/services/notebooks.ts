import { request, jsonInit } from '@/services/http'
import type { Notebook } from '@/types'

export function getNotebooksAPI(): Promise<Notebook[]> {
  return request<Notebook[]>('/api/notebooks')
}

export function createNotebookAPI(
  name: string = 'Untitled',
): Promise<Notebook> {
  return request<Notebook>(
    '/api/notebooks',
    jsonInit('POST', { notebook_name: name }),
  )
}

export function renameNotebookAPI(
  id: string,
  newName: string,
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/api/notebooks/${id}`,
    jsonInit('PUT', { notebook_name: newName }),
  )
}

export function deleteNotebookAPI(
  id: string,
): Promise<{ message: string }> {
  return request<{ message: string }>(`/api/notebooks/${id}`, {
    method: 'DELETE',
  })
}
