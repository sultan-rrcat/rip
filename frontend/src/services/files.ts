import { request, jsonInit } from '@/services/http'
import type { FileStatus, NotebookFile } from '@/types'

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
