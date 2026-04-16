import ChatArea from "../components/notebook/ChatArea"
import Footer from "../components/notebook/Footer"
import LeftSidebar from "../components/notebook/LeftSidebar"
import RightSidebar from "../components/notebook/RightSidebar"
import { useEffect, useState } from "react"
import { sendMessage } from "../services/llm"
import { sendMessageStream } from "../services/llm"
import { useParams } from "react-router-dom"
import { getMessagesAPI, createMessageAPI } from "../services/messages"
import { getFilesAPI, deleteFileAPI, uploadFileAPI } from "../services/files"
import { getNotebooksAPI, renameNotebookAPI } from "../services/notebooks"
import { v4 as uuidv4 } from 'uuid';


export default function Notebook() {
  const { id } = useParams()
  const [notebookName, setNotebookName] = useState("")
  const [files, setFiles] = useState([])
  const [messages, setMessages] = useState([{
    id: uuidv4(),
    role: "assistant",
    text: "How can I help you?",
    sources: []
  }])
  // const isLoading = messages.some(m => m.role === "loading")

  async function uploadFile(file) {
    const newFile = await uploadFileAPI(id, file)
    console.log(`newFile: ${newFile}`)
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

  // async function handleSendMessage(text) {
  //   // 1. Save user message to DB, get back the real message with its DB id
  //   const userMessage = await createMessageAPI(id, "user", text)

  //   // 2. Add user message + a temporary loading indicator to UI
  //   const loadingMessage = { id: uuidv4(), role: "loading", text: "..." }
  //   setMessages(prev => [...prev, userMessage, loadingMessage])

  //   try {
  //     // 3. Call the LLM
  //     const res = await sendMessage(text, id)

  //     // 4. Save assistant reply to DB
  //     const assistantMessage = await createMessageAPI(id, "assistant", res.chatbot_response, res.sources)

  //     // 5. Swap out the loading indicator with the real reply
  //     setMessages(prev =>
  //       prev.map(m =>
  //         m.id === loadingMessage.id
  //           ? assistantMessage
  //           : m
  //       )
  //     )
  //   } catch (err) {
  //     // 6. Save error to DB, swap out loading indicator
  //     const errorMessage = await createMessageAPI(id, "error", `Something went wrong. Error: ${err}`)
  //     setMessages(prev =>
  //       prev.map(m => m.id === loadingMessage.id ? errorMessage : m)
  //     )
  //   }
  // }
  async function handleSendMessage(text) {
    const userMessage = await createMessageAPI(id, "user", text)

    const assistantTempId = uuidv4()

    // Add user + empty assistant message (instead of loading)
    setMessages(prev => [
      ...prev,
      userMessage,
      {
        id: assistantTempId,
        role: "assistant",
        text: "thinking...",
        status: "thinking...",
        sources: []
      }
    ])

    let fullText = ""
    let finalSources = []

    try {
      let hasStartedStreaming = false
      await sendMessageStream(text, id, (chunk) => {
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
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantTempId
                ? { ...m, sources: chunk.sources }
                : m
            )
          )
        }
      })

      const current = messages.find(m => m.id === assistantTempId)

      // Save final response to DB
      const savedMessage = await createMessageAPI(
        id,
        "assistant",
        fullText,
        finalSources
      )

      // Replace temp message with DB message (real ID)
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantTempId ? savedMessage : m
        )
      )

    } catch (err) {
      const errorMessage = await createMessageAPI(
        id,
        "error",
        `Something went wrong. Error: ${err}`
      )

      setMessages(prev =>
        prev.map(m =>
          m.id === assistantTempId ? errorMessage : m
        )
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
        setMessages([{ id: uuidv4(), role: "assistant", text: "How can I help you?", sources: [] }])
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
        <Footer onSendMessage={handleSendMessage} />
      </main>

      {/* <RightSidebar
        files={files}
      /> */}
    </div>
  )
}