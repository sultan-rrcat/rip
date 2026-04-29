import { useState, useEffect, useCallback } from "react"
import { v4 as uuidv4 } from "uuid"
import { sendMessageStream } from "../../services/llm"
import { getMessagesAPI, createMessageAPI } from "../../services/messages"

const DEFAULT_MESSAGES = [
  {
    id: uuidv4(),
    role: "assistant",
    text: "How can I help you?",
    sources: []
  }
]

export function useMessages(notebook_id) {
  const [messages, setMessages] = useState(DEFAULT_MESSAGES)

  // Load persisted messages on mount
  useEffect(() => {
    if(!notebook_id) return
    let cancelled = false

    async function loadMessages() {
      try {
        const msgs = await getMessagesAPI(notebook_id)
        if (cancelled) return
        setMessages(msgs.length > 0 ? msgs : DEFAULT_MESSAGES)
      } catch (err) {
        console.error("Failed to load messages:", err)
      }
    }

    loadMessages()
    return () => { cancelled = true }
  }, [notebook_id])

  const handleSendMessage = useCallback(async (text)=> {
    // 1. Persist the user message
    const userMessage = await createMessageAPI(notebook_id, "user", text)

    // 2. Optimistically add user message + streaming placeholder
    const assistantTempId = uuidv4()
    setMessages(prev => [
      ...prev,
      userMessage,
      {
        id: assistantTempId,
        role: "assistant",
        text: "thinking...",
        status: "thinking",
        sources: []
      }
    ])

    let fullText = ""
    let finalSources = []

    try {
      // 3. Stream tokens in
      await sendMessageStream(text, notebook_id, (chunk) => {
        if (chunk.type === "token") {
          fullText += chunk.token
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantTempId
                ? { ...m, text: fullText, status: "streaming" }
                : m
            )
          )
        }
        if (chunk.type === "sources") {
          finalSources = chunk.sources
        }
      })

      // 4. Persist the final assistant message
      const savedMessage = await createMessageAPI(notebook_id, "assistant", fullText, finalSources)

      // 5. Swap placeholder with the real persisted message
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantTempId
            ? { ...savedMessage, status: "done" }
            : m
        )
      )
    } catch (err) {
      const errorMessage = await createMessageAPI(
        id,
        "error",
        `Something went wrong. Error: ${err}`
      )
      setMessages(prev =>
        prev.map(m => m.id === assistantTempId ? errorMessage : m)
      )
    }
  }, [notebook_id])
  return { messages, handleSendMessage }
}