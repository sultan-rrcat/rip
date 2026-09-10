import { API } from '@/config'
import type { FileStatus, NotebookFile } from '@/types'

export async function getFilesAPI(
  notebookId: string,
): Promise<NotebookFile[]> {
  console.log(`[files] GET /api/notebooks/${notebookId}/files`)
  const res = await fetch(`${API}/api/notebooks/${notebookId}/files`)
  const data: NotebookFile[] = await res.json()
  console.log(`[files] GET /api/notebooks/${notebookId}/files →`, data)
  return data
}

export async function createFileAPI(
  notebookId: string,
  fileName: string,
  fileSize: number,
): Promise<NotebookFile> {
  console.log(`[files] POST /api/notebooks/${notebookId}/files`, {
    fileName,
    fileSize,
  })
  const res = await fetch(`${API}/api/notebooks/${notebookId}/files`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_name: fileName, file_size: fileSize }),
  })
  const data: NotebookFile = await res.json()
  console.log(`[files] POST /api/notebooks/${notebookId}/files →`, data)
  return data
}

export async function updateFileStatusAPI(
  fileId: string,
  status: FileStatus,
): Promise<{ message: string }> {
  console.log(`[files] PATCH /api/files/${fileId}/status`, { status })
  const res = await fetch(`${API}/api/files/${fileId}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  const data: { message: string } = await res.json()
  console.log(`[files] PATCH /api/files/${fileId}/status →`, data)
  return data
}

export async function deleteFileAPI(
  fileId: string,
): Promise<{ message: string }> {
  console.log(`[files] DELETE /api/files/${fileId}`)
  const res = await fetch(`${API}/api/files/${fileId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete failed')
  const data: { message: string } = await res.json()
  console.log(`[files] DELETE /api/files/${fileId} →`, data)
  return data
}

export async function uploadFileAPI(
  notebookId: string,
  file: File,
): Promise<NotebookFile> {
  console.log('[files] POST/UPLOAD /api/files/upload')

  const formData = new FormData()
  formData.append('file', file)
  formData.append('notebook_id', notebookId)

  console.log(formData)

  const res = await fetch(`${API}/api/files/upload`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    console.error('Upload failed')
    throw new Error('Upload failed')
  }

  const data: NotebookFile = await res.json()
  return data
}
