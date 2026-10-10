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

export interface ConfigSnapshot {
  groups: ConfigGroup[]
  entries: Record<string, ConfigEntry>
  pending_restart: string[]
  pending_restart_command: string
  applied?: string[]
  restored?: string[]
}
