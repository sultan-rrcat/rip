import { useCallback, useEffect, useState } from 'react'
import { getRuntimeConfig, updateRuntimeConfig, resetRuntimeConfig } from '@/services/admin'
import { ApiError } from '@/services/http'
import type { ConfigSnapshot } from '@/types/admin'

export function useRuntimeConfig() {
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setSnapshot(await getRuntimeConfig())
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load runtime config')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const snap = await getRuntimeConfig()
        if (!cancelled) setSnapshot(snap)
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.detail : 'Failed to load runtime config')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  async function save(updates: Record<string, string | number | boolean>) {
    setSaving(true)
    setError('')
    try {
      const snap = await updateRuntimeConfig(updates)
      setSnapshot(snap)
      return snap
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : 'Save failed'
      setError(msg)
      throw err
    } finally {
      setSaving(false)
    }
  }

  async function reset(keys?: string[]) {
    setSaving(true)
    setError('')
    try {
      const snap = await resetRuntimeConfig(keys)
      setSnapshot(snap)
      return snap
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Reset failed')
      throw err
    } finally {
      setSaving(false)
    }
  }

  return { snapshot, loading, saving, error, setError, save, reset, reload: load }
}
