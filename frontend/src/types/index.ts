/* Shared domain types mirroring the backend API schema. */

import type { Artifact, Source } from './runs'

export type { Artifact, Source } from './runs'

export interface Notebook {
  notebook_id: string
  notebook_name: string
  created_at: string
}

export type FileStatus = 'uploading' | 'processing' | 'ready' | 'error'

export interface NotebookFile {
  id: string
  name: string
  size: number
  status: FileStatus
}

export type MessageRole = 'user' | 'assistant' | 'error'

export type MessageStatus = 'thinking' | 'streaming' | 'done'

export interface Message {
  id: string
  role: MessageRole
  text: string
  sources: Source[]
  artifacts?: Artifact[]
  created_at?: string
  status?: MessageStatus
}
