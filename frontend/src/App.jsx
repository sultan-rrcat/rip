import Header from "./components/Header"
import ChatArea from "./components/ChatArea"
import Footer from "./components/Footer"
import LeftSidebar from "./components/LeftSidebar"
import RightSidebar from "./components/RightSidebar"
import { useState } from "react"
import { sendMessage } from "./services/llm"

export default function App() {
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
      setFiles(prev => [...prev, ...newFiles])
      newFiles.forEach(f => processFile(f.id))
    }
    event.target.value = ''
  }

  function handleDelete(id) {
    // Update the files state by removing the file with the given id
    setFiles((previousFiles) => {
      // Step 1: Create a new array without the file that matches the given id

      const updatedFiles = []

      for (let i = 0; i < previousFiles.length; i++) {
        const file = previousFiles[i];
        if (file.id !== id) {
          updatedFiles.push(file);
        }
      }
      return updatedFiles;
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
    setFiles(prev => prev.map(f => f.id === fileId ? { ...f, status: 'processing' } : f
    )
    )
    //step2 simulate backend processing
    setTimeout(() => {
      const isError = Math.random() < 0.3 // 30% fail rate

      setFiles(prev =>
        prev.map(f =>
          f.id === fileId
            ? { ...f, status: isError ? 'error' : 'ready' }
            : f
        )
      )
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

    setMessages(prev => [...prev, userMessage, loadingMessage])

    try {
      const replyText = await sendMessage(text)

      const realReply = {
        id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
        role: "assistant",
        text: replyText
      }

      setMessages(prev => prev.map(m =>
        m.id === loadingMessage.id ? { ...realReply } : m
      ))
    } catch (err) {
      const errorReply = {
        id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
        role: "error",
        text: `Something went wrong. Please try again. Error: ${err}`
      }

      setMessages(prev => prev.map(m =>
        m.id === loadingMessage.id ? { ...errorReply } : m
      ))
    }
  }

  return (
    <div className="flex h-screen bg-gray-200 overflow-hidden">
      <LeftSidebar
        files={files}
        onUpload={handleUpload}
        onDelete={handleDelete}
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