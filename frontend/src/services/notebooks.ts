import { API } from '@/config'
import type { Notebook } from '@/types'

export async function getNotebooksAPI(): Promise<Notebook[]> {
  console.log('[notebooks] GET /api/notebooks')
  const res = await fetch(`${API}/api/notebooks`)
  const data: Notebook[] = await res.json()
  console.log('[notebooks] GET /api/notebooks →', data)
  return data
}

export async function createNotebookAPI(
  name: string = 'Untitled',
): Promise<Notebook> {
  console.log('[notebooks] POST /api/notebooks', { name })
  const res = await fetch(`${API}/api/notebooks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notebook_name: name }),
  })
  const data: Notebook = await res.json()
  console.log('[notebooks] POST /api/notebooks →', data)
  return data
}

export async function renameNotebookAPI(
  id: string,
  newName: string,
): Promise<{ message: string }> {
  console.log(`[notebooks] PUT /api/notebooks/${id}`, { newName })
  const res = await fetch(`${API}/api/notebooks/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notebook_name: newName }),
  })
  if (!res.ok) throw new Error('Rename failed')
  const data: { message: string } = await res.json()
  console.log(`[notebooks] PUT /api/notebooks/${id} →`, data)
  return data
}

export async function deleteNotebookAPI(
  id: string,
): Promise<{ message: string }> {
  console.log(`[notebooks] DELETE /api/notebooks/${id}`)
  const res = await fetch(`${API}/api/notebooks/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete failed')
  const data: { message: string } = await res.json()
  console.log(`[notebooks] DELETE /api/notebooks/${id} →`, data)
  return data
}
