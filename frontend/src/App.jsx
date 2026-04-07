import Header from "./components/Header"
import ChatArea from "./components/ChatArea"
import Footer from "./components/Footer"
import Sidebar from "./components/Sidebar"
import { useState } from "react"
import { useEffect } from "react"
import { sendMessage } from "./services/llm"

export default function App() {
  const [files, setFiles] = useState([])
  const [activeFileIds, setActiveFileIds] = useState([])
  const [messages, setMessages] = useState([{
    id: crypto.randomUUID(),
    role: "assistant",
    text: "How can I help you?"
  }])
  const isLoading = messages.some(m=>m.role==="loading")

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
        id: crypto.randomUUID(),
        name: file.name,
        size: formatSize(file.size),
        file: file,
        status: ''
      }

      newFiles.push(newFile)
    }

    if (newFiles.length > 0) {
      setFiles(prev => [...prev, ...newFiles])
      // setActiveFileIds(prev => [...prev, ...newFiles.map(f => f.id)])

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

      // Step 2: Update activeFileIds state to remove the deleted file’s id
      setActiveFileIds((prevActiveFileIds) => {
        const newActiveFileIds = [];
        for (let j = 0; j < prevActiveFileIds.length; j++) {
          const fileId = prevActiveFileIds[j];
          if (fileId !== id) {
            newActiveFileIds.push(fileId)
          }
        }
        return newActiveFileIds;
      })
      return updatedFiles;
    });
  }

  function handleFileSelect(fileId) {
    setActiveFileIds(prev => {
      // If already selected, remove it
      if (prev.includes(fileId)) {
        return prev.filter(id => id !== fileId)
      } else { //If not add them
        triggerProcessingIfNeeded(fileId)
        return [...prev, fileId]
      }
    })
  }

  function handleSelectAll() {
    // Step1: check if all files are already selected deselect all
    if (activeFileIds.length === files.length) {
      setActiveFileIds([])
    } else { //Otherwise select all
      const allIds = files.map(f => f.id)
      allIds.forEach(id => {
        triggerProcessingIfNeeded(id)
      })
      setActiveFileIds(allIds) //loops through the files array and builds a new array containing only the file id and finally pass it to setActiveFileIds
    }
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  function triggerProcessingIfNeeded(fileId) {
    const file = files.find(f => f.id === fileId)
    console.log(`file stauts: ${file.status}`)
    if (!file || file.status === "ready" || file.status === "processing") {
      return
    }
    processFile(fileId)
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
      id: crypto.randomUUID(),
      role: "user",
      text: text
    }

    const loadingMessage = {
      id: crypto.randomUUID(),
      role: "loading",
      text: "..."
    }

    setMessages(prev => [...prev, userMessage, loadingMessage])

    try {
      const replyText = await sendMessage(text)

      const realReply = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: replyText
      }

      setMessages(prev => prev.map(m =>
        m.id === loadingMessage.id ? { ...realReply } : m
      ))
    } catch (err) {
      const errorReply = {
        id: crypto.randomUUID(),
        role: "error",
        text: `Something went wrong. Please try again. Error: ${err}`
      }

      setMessages(prev => prev.map(m =>
        m.id === loadingMessage.id ? { ...errorReply } : m
      ))
    }
  }

  useEffect(() => {
    setActiveFileIds(prevActiveIds => {
      return prevActiveIds.filter(id => {
        const file = files.find(f => f.id === id)
        return file && file.status === 'ready'
      })
    })
  }, [files])

  return (
    <div className="flex h-screen bg-gray-200 overflow-hidden">
      <Sidebar
        files={files}
        onUpload={handleUpload}
        onDelete={handleDelete}
        activeFileIds={activeFileIds}
        onFileSelect={handleFileSelect}
        onSelectAll={handleSelectAll}
      />
      <main className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <ChatArea
          activeFile={files.find(f => activeFileIds.includes(f.id))}
          messages={messages}
        />
        <Footer onSendMessage={handleSendMessage} isLoading={isLoading}/>
      </main>
    </div>
  )
}