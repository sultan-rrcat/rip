export type ConfigValue = string | number | boolean

export interface ConfigEntry {
  key: string
  group: string
  label: string
  description: string
  type: 'int' | 'float' | 'bool' | 'str' | 'enum'
  apply: 'live' | 'restart'
  editable: boolean
  secret?: boolean
  configured?: boolean
  value: ConfigValue
  default: ConfigValue
  source: 'default' | 'env' | 'db'
  options?: string[] | null
  min?: number | null
  max?: number | null
  updated_by?: string | null
  updated_at?: string | null
}

export interface ConfigGroup {
  id: string
  title: string
  description: string
  apply: 'live' | 'mixed' | 'system'
  fields: string[]
}

export interface HostedModel {
  id: string
  display_name: string
}

export interface HostedModels {
  reachable: boolean
  models: HostedModel[]
}

export interface PromptEntry {
  key: string
  group: string
  label: string
  description: string
  value: string
  seed: string
  source: 'seed' | 'db'
  placeholders: string[]
  updated_by?: string | null
  updated_at?: string | null
}

export interface PromptGroup {
  id: string
  title: string
  description: string
  prompts: string[]
}

export interface ToolEntry {
  tool_id: string
  name: string
  description: string
  description_source: 'seed' | 'db'
  enabled: boolean
  effect_class: string
  cost_class: string
  updated_by?: string | null
  updated_at?: string | null
}

export interface PromptsSnapshot {
  groups: PromptGroup[]
  prompts: Record<string, PromptEntry>
  tools: Record<string, ToolEntry>
  applied?: string[]
  restored?: string[]
  toggled?: Record<string, boolean>
}

export interface ConfigSnapshot {
  groups: ConfigGroup[]
  entries: Record<string, ConfigEntry>
  pending_restart: string[]
  pending_restart_command: string
  applied?: string[]
  restored?: string[]
}
