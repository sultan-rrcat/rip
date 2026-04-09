// import Header from "../components/Header"
import ChatArea from "../components/notebook/ChatArea"
import Footer from "../components/notebook/Footer"
import LeftSidebar from "../components/notebook/LeftSidebar"
import RightSidebar from "../components/notebook/RightSidebar"
import { useEffect, useState } from "react"
import { sendMessage } from "../services/llm"
import { useParams } from "react-router-dom"

export default function Notebook() {
  const { id } = useParams()
  const [notebookName, setNotebookName] = useState("")
  const [files, setFiles] = useState([])
  const [messages, setMessages] = useState([{
    id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
    role: "assistant",
    text: "How can I help you?"
  }])
  const isLoading = messages.some(m => m.role === "loading")

  function handleUpload(event) {
    const picked = event.target.files
    if (!picked || picked.length === 0) return
    const newFiles = []

    for (let i = 0; i < picked.length; i++) {
      const file = picked[i]

      if (!file.name.toLowerCase().endsWith('.pdf')) {
        alert(`"${file.name}" is not a PDF file, skipping...`)
        continue
      }

      let fileExists = false
      for (let i = 0; i < files.length; i++) {
        const oldFile = files[i]
        if (oldFile.name === file.name) {
          fileExists = true;
          break;
        }
      }

      if (fileExists) {
        alert(`"${file.name}" is already uploaded.`)
        event.target.value = ''
        continue
      }

      const newFile = {
        id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
        name: file.name,
        size: formatSize(file.size),
        file: file,
        status: ''
      }
      newFiles.push(newFile)
    }

    if (newFiles.length > 0) {
      setFiles(prev => {
        const updated = [...prev, ...newFiles]
        updateNotebook({ files: updated })
        return updated
      })
      newFiles.forEach(f => processFile(f.id))
    }
    event.target.value = ''
  }

  function handleDelete(id) {
    // Update the files state by removing the file with the given id
    setFiles((prev) => {
      const updated = prev.filter(file => file.id !== id)
      updateNotebook({ files: updated })
      return updated;
    });
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  function processFile(fileId) {

    console.log(`triggering process files.`)
    //step1 mark as processing
    setFiles(prev => {
      const updated = prev.map(f => f.id === fileId ? { ...f, status: 'processing' } : f)
      updateNotebook({ files: updated })
      return updated
    })
    //step2 simulate backend processing
    setTimeout(() => {
      const isError = Math.random() < 0.3 // 30% fail rate

      setFiles(prev => {
        const updated = prev.map(f =>
          f.id === fileId
            ? { ...f, status: isError ? 'error' : 'ready' }
            : f
        )
        updateNotebook({ files: updated })
        return updated
      })
    }, 5000)
  }

  async function handleSendMessage(text) {
    const userMessage = {
      id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
      role: "user",
      text: text
    }

    const loadingMessage = {
      id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
      role: "loading",
      text: "..."
    }

    setMessages(prev => {
      const updated = [...prev, userMessage, loadingMessage]
      updateNotebook({ messages: updated })
      return updated
    })


    try {
      const replyText = await sendMessage(text)

      const realReply = {
        id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
        role: "assistant",
        text: replyText
      }

      setMessages(prev => {
        const updated = prev.map(m =>
          m.id === loadingMessage.id ? { ...realReply } : m)
        updateNotebook({ messages: updated })
        return updated
      })
    } catch (err) {
      const errorReply = {
        id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
        role: "error",
        text: `Something went wrong. Please try again. Error: ${err}`
      }

      setMessages(prev => {
        const updated = prev.map(m =>
          m.id === loadingMessage.id ? { ...errorReply } : m)
        updateNotebook({ messages: updated })
        return updated
      })
    }
  }

  function updateNotebook(updatedData) {
    const stored = JSON.parse(localStorage.getItem("notebooks")) || []
    const updated = stored.map(n => n.id === id ? { ...n, ...updatedData } : n)
    localStorage.setItem("notebooks", JSON.stringify(updated))
  }

  function renameNotebook(newName){
    const stored = JSON.parse(localStorage.getItem("notebooks")) || []
    const updated = stored.map(n=>n.id===id?{...n, name:newName}:n)
    localStorage.setItem("notebooks", JSON.stringify(updated))
    setNotebookName(newName)
  }

  useEffect(() => {
    const stored = JSON.parse(localStorage.getItem("notebooks")) || []
    const notebook = stored.find(n => n.id === id)
    if (notebook) {
      setFiles(notebook.files || [])
      setMessages(notebook.messages || [])
      setNotebookName(notebook.name||"Untitled")
    }
  }, [id])


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