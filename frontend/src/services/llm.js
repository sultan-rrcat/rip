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


export async function sendMessageStream(text, notebook_id, onToken) {
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

        // Split SSE messages
        const lines = buffer.split("\n\n")
        buffer = lines.pop()

        for (const line of lines) {
            if (line.startsWith("data:")) {
                const data = line.replace("data:", "").trim()

                if (data === "[DONE]") {
                    return
                }

                try {
                    const parsed = JSON.parse(data)
                    onToken(parsed.response)   // 👈 stream token here
                } catch (err) {
                    console.error("Parse error:", err)
                }
            }
        }
    }
}