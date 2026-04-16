const API = "http://localhost:5000"

export async function getMessagesAPI(notebookId) {
  console.log(`[messages] GET /api/notebooks/${notebookId}/messages`)
  const res = await fetch(`${API}/api/notebooks/${notebookId}/messages`)
  const data = await res.json()
  console.log(`[messages] GET /api/notebooks/${notebookId}/messages →`, data)
  return data
}

export async function createMessageAPI(notebookId, role, text, sources) {
  console.log(`[messages] POST /api/notebooks/${notebookId}/messages`, { role, text, sources })
  const res = await fetch(`${API}/api/notebooks/${notebookId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, text, sources })
  })
  const data = await res.json()
  console.log(`[messages] POST /api/notebooks/${notebookId}/messages →`, data)
  return data
}