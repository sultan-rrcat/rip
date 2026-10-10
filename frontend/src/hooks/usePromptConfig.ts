import { useCallback, useEffect, useState } from 'react'
import {
  getPrompts,
  updatePrompts,
  resetPrompts,
  setToolEnabled,
  setToolDescription,
  resetTools,
} from '@/services/admin'
import { ApiError } from '@/services/http'
import type { PromptsSnapshot } from '@/types/admin'

export function usePromptConfig() {
  const [snapshot, setSnapshot] = useState<PromptsSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const snap = await getPrompts()
        if (!cancelled) setSnapshot(snap)
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.detail : 'Failed to load prompts')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const reload = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setSnapshot(await getPrompts())
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load prompts')
    } finally {
      setLoading(false)
    }
  }, [])

  async function guard<T>(fn: () => Promise<T>): Promise<T> {
    setSaving(true)
    setError('')
    try {
      return await fn()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Request failed')
      throw err
    } finally {
      setSaving(false)
    }
  }

  async function save(updates: Record<string, string>) {
    const snap = await guard(() => updatePrompts(updates))
    setSnapshot(snap)
    return snap
  }

  async function reset(keys?: string[]) {
    const snap = await guard(() => resetPrompts(keys))
    setSnapshot(snap)
    return snap
  }

  async function toggleTool(toolId: string, enabled: boolean) {
    const snap = await guard(() => setToolEnabled(toolId, enabled))
    setSnapshot(snap)
    return snap
  }

  async function saveToolDescription(toolId: string, description: string) {
    const snap = await guard(() => setToolDescription(toolId, description))
    setSnapshot(snap)
    return snap
  }

  async function resetToolSet(toolIds?: string[]) {
    const snap = await guard(() => resetTools(toolIds))
    setSnapshot(snap)
    return snap
  }

  return { snapshot, loading, saving, error, setError, save, reset, toggleTool, saveToolDescription, resetToolSet, reload }
}
