import { request, jsonInit } from '@/services/http'
import type {
  StudioConvertFormat,
  StudioJob,
  StudioQuizKind,
} from '@/types/studio'

export async function createQuizJobAPI(
  notebookId: string,
  count: number,
  kind: StudioQuizKind,
): Promise<StudioJob> {
  return request<StudioJob>(
    `/api/notebooks/${notebookId}/studio/quiz`,
    jsonInit('POST', { count, kind }),
  )
}

export async function createConvertJobAPI(
  notebookId: string,
  fileId: string | '*',
  targetFormat: StudioConvertFormat,
): Promise<StudioJob> {
  return request<StudioJob>(
    `/api/notebooks/${notebookId}/studio/convert`,
    jsonInit('POST', { file_id: fileId, target_format: targetFormat }),
  )
}

export function listStudioJobsAPI(notebookId: string): Promise<StudioJob[]> {
  return request<StudioJob[]>(`/api/notebooks/${notebookId}/studio/jobs`)
}

export function getStudioJobAPI(
  notebookId: string,
  jobId: string,
): Promise<StudioJob> {
  return request<StudioJob>(
    `/api/notebooks/${notebookId}/studio/jobs/${jobId}`,
  )
}

export function cancelStudioJobAPI(
  notebookId: string,
  jobId: string,
): Promise<{ job_id: string; run_id: string; status: string; already_done: boolean }> {
  return request(
    `/api/notebooks/${notebookId}/studio/jobs/${jobId}/cancel`,
    jsonInit('POST', {}),
  )
}
