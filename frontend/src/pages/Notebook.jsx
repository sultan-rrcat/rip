import ChatArea from "../components/notebook/ChatArea"
import Footer from "../components/notebook/Footer"
import LeftSidebar from "../components/notebook/LeftSidebar"
import RightSidebar from "../components/notebook/RightSidebar"
import { useEffect, useState } from "react"
import { sendMessage } from "../services/llm"
import { useParams } from "react-router-dom"
import { getMessagesAPI, createMessageAPI } from "../services/messages"
import { getFilesAPI, createFileAPI, updateFileStatusAPI, deleteFileAPI } from "../services/files"
import { getNotebooksAPI, renameNotebookAPI } from "../services/notebooks"
import { v4 as uuidv4 } from 'uuid';


export default function Notebook() {
  const { id } = useParams()
  const [notebookName, setNotebookName] = useState("")
  const [files, setFiles] = useState([])
  const [messages, setMessages] = useState([{
    id: uuidv4(),
    role: "assistant",
    text: "How can I help you?"
  }])
  const isLoading = messages.some(m => m.role === "loading")

  async function uploadFile(file) {
    // 1. Register file in DB, it starts as 'processing'
    // const newFile = await createFileAPI(id, file.name, file.size)

    // 2. Add to UI immediately so user sees it with a spinner
    // setFiles(prev => [...prev, newFile])

    // // 3. Simulate processing (replace with real logic later)
    // setTimeout(async () => {
    //   const status = Math.random() < 0.3 ? "error" : "ready"

    //   // 4. Update status in DB
    //   await updateFileStatusAPI(newFile.id, status)

    //   // 5. Update status in UI
    //   setFiles(prev =>
    //     prev.map(f => f.id === newFile.id ? { ...f, status } : f)
    //   )
    // }, 5000)

    const formData = new FormData()
    formData.append("file", file)
    formData.append("notebook_id", id)

    const res = await fetch("http://localhost:5000/api/files/upload", {
      method: "POST",
      body: formData
    })

    if (!res.ok) {
      console.error("Upload failed")
      const newFile = { id: uuidv4(), name: file.name, status: "error" }
      setFiles(prev => [...prev, newFile])
      return
    }

    const newFile = await res.json()
    setFiles(prev => [...prev, newFile])

    try {
      await fetch(`http://localhost:5000/api/files/${newFile.id}/process`, {
        method: "POST"
      })
    } catch (err) {
      console.error(err)

      setFiles(prev =>
        prev.map(f => f.id === newFile.id ? { ...f, status: "error" } : f)
      )
    }
  }

  async function handleUpload(event) {
    const picked = event.target.files
    if (!picked || picked.length === 0) return
    await Promise.all([...picked].map(uploadFile))
    event.target.value = ''
  }

  async function handleDelete(fileId) {
    // 1. Delete from DB
    await deleteFileAPI(fileId)

    // 2. Remove from UI
    setFiles(prev => prev.filter(f => f.id !== fileId))
  }

  async function handleSendMessage(text) {
    // 1. Save user message to DB, get back the real message with its DB id
    const userMessage = await createMessageAPI(id, "user", text)

    // 2. Add user message + a temporary loading indicator to UI
    const loadingMessage = { id: uuidv4(), role: "loading", text: "..." }
    setMessages(prev => [...prev, userMessage, loadingMessage])

    try {
      // 3. Call the LLM
      const replyText = await sendMessage(text, id)

      // 4. Save assistant reply to DB
      const assistantMessage = await createMessageAPI(id, "assistant", replyText)

      // 5. Swap out the loading indicator with the real reply
      setMessages(prev =>
        prev.map(m => m.id === loadingMessage.id ? assistantMessage : m)
      )
    } catch (err) {
      // 6. Save error to DB, swap out loading indicator
      const errorMessage = await createMessageAPI(id, "error", `Something went wrong. Error: ${err}`)
      setMessages(prev =>
        prev.map(m => m.id === loadingMessage.id ? errorMessage : m)
      )
    }
  }

  async function renameNotebook(newName) {
    if (!newName.trim()) return
    try {
      await renameNotebookAPI(id, newName)
      setNotebookName(newName)
    } catch (error) {
      console.error(error)
    }
  }

  useEffect(() => {
    let cancelled = false

    async function loadNotebook() {
      const notebooks = await getNotebooksAPI()
      const notebook = notebooks.find(n => n.notebook_id === id)
      if (!cancelled && notebook) setNotebookName(notebook.notebook_name)

      const msgs = await getMessagesAPI(id)
      if (cancelled) return
      if (msgs.length === 0) {
        setMessages([{ id: uuidv4(), role: "assistant", text: "How can I help you?" }])
      } else {
        setMessages(msgs)
      }

      const fetchedFiles = await getFilesAPI(id)
      if (!cancelled) setFiles(fetchedFiles)
    }

    loadNotebook()

    return () => { cancelled = true }
  }, [id])


  useEffect(() => {
    let interval
    const hasProcessing = files.some(f => f.status === "processing")

    if (hasProcessing) {
      interval = setInterval(async () => {
        const updatedFiles = await getFilesAPI(id)
        setFiles(updatedFiles)
      }, 2000)
    }

    return () => clearInterval(interval)
  }, [files]) // ← depend on the full files array

  return (
    <div className="flex h-screen bg-gray-200 overflow-hidden">
      <LeftSidebar
        files={files}
        onUpload={handleUpload}
        onDelete={handleDelete}
        notebookName={notebookName}
        onRenameNotebook={renameNotebook}
      />
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* <Header /> */}
        <ChatArea
          messages={messages}
        />
        <Footer onSendMessage={handleSendMessage} isLoading={isLoading} />
      </main>

      <RightSidebar
        files={files}
      />
    </div>
  )
}