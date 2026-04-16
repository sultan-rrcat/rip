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