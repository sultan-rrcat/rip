import { request, jsonInit } from '@/services/http'
import type { ConfigSnapshot } from '@/types/admin'

export function getRuntimeConfig(): Promise<ConfigSnapshot> {
  return request<ConfigSnapshot>('/v1/admin/config')
}

export function updateRuntimeConfig(updates: Record<string, string | number | boolean>): Promise<ConfigSnapshot> {
  return request<ConfigSnapshot>('/v1/admin/config', jsonInit('PUT', { updates }))
}

export function resetRuntimeConfig(keys?: string[]): Promise<ConfigSnapshot> {
  return request<ConfigSnapshot>(
    '/v1/admin/config/reset',
    jsonInit('POST', keys ? { keys } : {}),
  )
}
