import type { Artifact } from '@/types/runs'

export type StudioKind = 'quiz' | 'convert'
export type StudioQuizKind = 'mcq' | 'msq' | 'truefalse'
export type StudioConvertFormat = 'md' | 'docx' | 'pdf'
export type StudioJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface StudioJob {
  job_id: string
  notebook_id: string
  kind: StudioKind
  params: Record<string, string | number>
  run_id: string
  status: StudioJobStatus
  created_at: string | null
  artifacts: Artifact[]
  /** Aggregator verdict (success/partial/failed, "" while running). */
  result_status: string
  /** Final run text — the answer, clarification, or failure lines. */
  summary: string | null
}

export function isTerminalStatus(status: string): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled'
}
