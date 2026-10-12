import { API } from '@/config'
import { request, jsonInit } from '@/services/http'
import type { FileStatus, NotebookFile, FilePreview } from '@/types'

export function getFilesAPI(notebookId: string): Promise<NotebookFile[]> {
  return request<NotebookFile[]>(`/api/notebooks/${notebookId}/files`)
}

export function createFileAPI(
  notebookId: string,
  fileName: string,
  fileSize: number,
): Promise<NotebookFile> {
  return request<NotebookFile>(
    `/api/notebooks/${notebookId}/files`,
    jsonInit('POST', { file_name: fileName, file_size: fileSize }),
  )
}

export function updateFileStatusAPI(
  fileId: string,
  status: FileStatus,
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/api/files/${fileId}/status`,
    jsonInit('PATCH', { status }),
  )
}

export function deleteFileAPI(fileId: string): Promise<{ message: string }> {
  return request<{ message: string }>(`/api/files/${fileId}`, {
    method: 'DELETE',
  })
}

export function uploadFileAPI(
  notebookId: string,
  file: File,
): Promise<NotebookFile> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('notebook_id', notebookId)

  return request<NotebookFile>('/api/files/upload', {
    method: 'POST',
    body: formData,
  })
}

export function processFileAPI(fileId: string): Promise<{ message: string }> {
  return request<{ message: string }>(`/api/files/${fileId}/process`, {
    method: 'POST',
  })
}

export function getFilePreviewAPI(fileId: string): Promise<FilePreview> {
  return request<FilePreview>(`/api/files/${fileId}/preview`)
}

/** Direct download URL for anchor hrefs (cookie auth flows same-origin). */
export function fileDownloadUrl(fileId: string): string {
  return `${API}/api/files/${fileId}/download`
}

/** Fetch raw bytes (pdf blob preview) — services layer only. */
export async function fetchFileBlob(fileId: string): Promise<Blob> {
  const res = await fetch(`${API}/api/files/${fileId}/download`, {
    credentials: 'include',
  })
  if (res.status === 401) {
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }
  if (!res.ok) throw new Error(`Download failed: ${res.status}`)
  return await res.blob()
}
