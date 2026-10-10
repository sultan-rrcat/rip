import { request, jsonInit } from '@/services/http'
import type { ConfigSnapshot, HostedModels, PromptsSnapshot } from '@/types/admin'

export function getRuntimeConfig(): Promise<ConfigSnapshot> {
  return request<ConfigSnapshot>('/v1/admin/config')
}

export function getHostedModels(): Promise<HostedModels> {
  return request<HostedModels>('/v1/admin/models')
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

export function getPrompts(): Promise<PromptsSnapshot> {
  return request<PromptsSnapshot>('/v1/admin/prompts')
}

export function updatePrompts(updates: Record<string, string>): Promise<PromptsSnapshot> {
  return request<PromptsSnapshot>('/v1/admin/prompts', jsonInit('PUT', { updates }))
}

export function resetPrompts(keys?: string[]): Promise<PromptsSnapshot> {
  return request<PromptsSnapshot>(
    '/v1/admin/prompts/reset',
    jsonInit('POST', keys ? { keys } : {}),
  )
}

export function setToolEnabled(toolId: string, enabled: boolean): Promise<PromptsSnapshot> {
  return request<PromptsSnapshot>(
    `/v1/admin/tools/${encodeURIComponent(toolId)}/enabled`,
    jsonInit('PUT', { enabled }),
  )
}

export function setToolDescription(toolId: string, description: string): Promise<PromptsSnapshot> {
  return request<PromptsSnapshot>(
    `/v1/admin/tools/${encodeURIComponent(toolId)}/description`,
    jsonInit('PUT', { description }),
  )
}

export function resetTools(toolIds?: string[]): Promise<PromptsSnapshot> {
  return request<PromptsSnapshot>(
    '/v1/admin/tools/reset',
    jsonInit('POST', toolIds ? { tool_ids: toolIds } : {}),
  )
}
