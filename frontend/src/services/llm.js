const LLM_URL = import.meta.env.VITE_LLM_URL

console.log("LLM_URL:", LLM_URL)
export async function sendMessage(text, notebook_id){
    const response = await fetch(`${LLM_URL}/api/prompt`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({notebook_id: notebook_id, prompt: text})
    })

    if(!response.ok){
        throw new Error(`Server responded with ${response.status}`)
    }
    const data = await response.json()
    return {
        chatbot_response: data.chatbot_response,
        sources: data.sources || []
    }
}


export async function sendMessageStream(text, notebook_id, onChunk) {
    const response = await fetch(`${LLM_URL}/api/prompt/stream`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            notebook_id: notebook_id,
            prompt: text
        })
    })

    if (!response.ok) {
        throw new Error(`Server responded with ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder("utf-8")

    let buffer = ""

    while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split("\n\n")
        buffer = lines.pop()

        for (const line of lines) {
            if (!line.startsWith("data:")) continue

            const data = line.replace("data:", "").trim()

            if (data === "[DONE]") {
                onChunk({ type: "done" })
                return
            }

            try {
                const parsed = JSON.parse(data)

                // 🔥 Handle token
                if (parsed.response) {
                    onChunk({
                        type: "token",
                        token: parsed.response
                    })
                }

                // 🔥 Handle sources
                if (parsed.type === "sources") {
                    onChunk({
                        type: "sources",
                        sources: parsed.sources || []
                    })
                }

            } catch (err) {
                console.error("Parse error:", err)
            }
        }
    }
}