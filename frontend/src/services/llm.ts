import { API } from '@/config'
import type { Source, StreamChunkHandler } from '@/types'

console.log('BACKEND API URL:', API)

interface SendMessageResponse {
  chatbot_response: string
  sources: Source[]
}

export async function sendMessage(
  text: string,
  notebook_id: string,
): Promise<SendMessageResponse> {
  const response = await fetch(`${API}/api/prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notebook_id: notebook_id, prompt: text }),
  })

  if (!response.ok) {
    throw new Error(`Server responded with ${response.status}`)
  }
  const data = await response.json()
  return {
    chatbot_response: data.chatbot_response,
    sources: data.sources || [],
  }
}

interface StreamEvent {
  response?: string
  type?: string
  sources?: Source[]
}

export async function sendMessageStream(
  text: string,
  notebook_id: string,
  onChunk: StreamChunkHandler,
): Promise<void> {
  const response = await fetch(`${API}/api/prompt/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      notebook_id: notebook_id,
      prompt: text,
    }),
  })

  if (!response.ok) {
    throw new Error(`Server responded with ${response.status}`)
  }

  if (!response.body) {
    throw new Error('Response body is empty')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')

  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data:')) continue

      const data = line.replace('data:', '').trim()

      if (data === '[DONE]') {
        onChunk({ type: 'done' })
        return
      }

      try {
        const parsed: StreamEvent = JSON.parse(data)

        // 🔥 Handle token
        if (parsed.response) {
          onChunk({
            type: 'token',
            token: parsed.response,
          })
        }

        // 🔥 Handle sources
        if (parsed.type === 'sources') {
          onChunk({
            type: 'sources',
            sources: parsed.sources || [],
          })
        }
      } catch (err) {
        console.error('Parse error:', err)
      }
    }
  }
}
