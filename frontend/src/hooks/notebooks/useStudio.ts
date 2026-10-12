import { useCallback, useEffect, useRef, useState } from 'react'
import {
  cancelStudioJobAPI,
  createConvertJobAPI,
  getStudioJobAPI,
  listStudioJobsAPI,
} from '@/services/studio'
import { notifyToast } from '@/services/toast'
import type {
  StudioConvertFormat,
  StudioJob,
} from '@/types/studio'
import { isTerminalStatus } from '@/types/studio'

const POLL_MS = 2500

function jobErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  return 'Studio request failed'
}

/**
 * Studio panel state: job history + create/cancel, with polling refresh.
 *
 * History is server truth (GET jobs joins live `runs` status + persisted
 * `artifacts` events), so reloads and reconnects need no local replay —
 * unlike chat runs, no SSE subscription is required. Non-terminal jobs
 * are polled until they settle.
 */
export function useStudio(notebookId: string | undefined) {
  const [jobs, setJobs] = useState<StudioJob[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState<null | 'convert'>(null)
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const nbRef = useRef<string | undefined>(notebookId)

  useEffect(() => {
    nbRef.current = notebookId
  }, [notebookId])

  const clearTimer = useCallback((jobId: string) => {
    const t = timers.current.get(jobId)
    if (t) {
      clearTimeout(t)
      timers.current.delete(jobId)
    }
  }, [])

  const clearAllTimers = useCallback(() => {
    for (const t of timers.current.values()) clearTimeout(t)
    timers.current.clear()
  }, [])

  const pollRef = useRef<(jobId: string) => void>(() => {})
  const pollJob = useCallback(
    (jobId: string) => {
      const nb = nbRef.current
      if (!nb) return
      clearTimer(jobId)
      const timer = setTimeout(() => {
        void getStudioJobAPI(nb, jobId)
          .then((fresh) => {
            setJobs((prev) => prev.map((j) => (j.job_id === jobId ? fresh : j)))
            if (!isTerminalStatus(fresh.status)) pollRef.current(jobId)
          })
          .catch(() => {
            // Transient poll failure: retry once more unless unmounted.
            if (timers.current.has(jobId)) pollRef.current(jobId)
          })
      }, POLL_MS)
      timers.current.set(jobId, timer)
    },
    [clearTimer],
  )

  useEffect(() => {
    pollRef.current = pollJob
  }, [pollJob])

  const reload = useCallback(async () => {
    if (!notebookId) return
    setLoading(true)
    setError(null)
    try {
      const list = await listStudioJobsAPI(notebookId)
      setJobs(list)
      for (const j of list) {
        if (!isTerminalStatus(j.status)) pollJob(j.job_id)
      }
    } catch (err) {
      setError(jobErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [notebookId, pollJob])

  useEffect(() => {
    setJobs([])
    setError(null)
    clearAllTimers()
    if (!notebookId) return
    void reload()
    return () => clearAllTimers()
  }, [notebookId, reload, clearAllTimers])

  const startConvertJob = useCallback(
    async (
      fileId: string | '*',
      targetFormat: StudioConvertFormat,
    ): Promise<StudioJob | null> => {
      if (!notebookId || starting) return null
      setStarting('convert')
      try {
        const job = await createConvertJobAPI(notebookId, fileId, targetFormat)
        setJobs((prev) => [job, ...prev])
        pollJob(job.job_id)
        return job
      } catch (err) {
        notifyToast(jobErrorMessage(err))
        return null
      } finally {
        setStarting(null)
      }
    },
    [notebookId, starting, pollJob],
  )

  const cancelJob = useCallback(
    async (jobId: string) => {
      if (!notebookId) return
      try {
        await cancelStudioJobAPI(notebookId, jobId)
        const fresh = await getStudioJobAPI(notebookId, jobId)
        clearTimer(jobId)
        setJobs((prev) => prev.map((j) => (j.job_id === jobId ? fresh : j)))
      } catch (err) {
        notifyToast(jobErrorMessage(err))
      }
    },
    [notebookId, clearTimer],
  )

  return {
    jobs,
    loading,
    error,
    starting,
    isBusy: starting !== null,
    reload,
    startConvertJob,
    cancelJob,
  }
}
