/* Shared domain types mirroring the backend API schema. */

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

export type MessageRole = 'user' | 'assistant' | 'error' | 'loading'

export type MessageStatus = 'thinking' | 'streaming' | 'done'

export interface Source {
  source: string
  section: string
}

export interface MessageArtifact {
  artifact_id: string
  kind: string
  filename: string
  url: string
}

export interface Message {
  id: string
  role: MessageRole
  text: string
  sources: Source[]
  artifacts?: MessageArtifact[]
  created_at?: string
  status?: MessageStatus
}

/* SSE stream chunks emitted by POST /api/prompt/stream */

export interface StreamTokenChunk {
  type: 'token'
  token: string
}

export interface StreamSourcesChunk {
  type: 'sources'
  sources: Source[]
}

export interface StreamDoneChunk {
  type: 'done'
}

export type StreamChunk =
  | StreamTokenChunk
  | StreamSourcesChunk
  | StreamDoneChunk

export type StreamChunkHandler = (chunk: StreamChunk) => void
