import { useState, useEffect } from "react"
import { getFilesAPI, deleteFileAPI, uploadFileAPI } from "../../services/files"
import { API } from "../../config"

export function useFiles(notebook_id) {
  const [files, setFiles] = useState([])

  // Initial fetch — guard against undefined id (first render race)
  useEffect(() => {
    if (!notebook_id) return
    let cancelled = false

    async function loadFiles() {
      try {
        const fetchedFiles = await getFilesAPI(notebook_id)
        // Guard: API might return an error object instead of an array
        if (!cancelled) setFiles(Array.isArray(fetchedFiles) ? fetchedFiles : [])
      } catch (err) {
        console.error("Failed to load files:", err)
      }
    }

    loadFiles()
    return () => { cancelled = true }
  }, [notebook_id])

  // Poll while any file is still processing
  useEffect(() => {
    if (!notebook_id) return
    const isProcessing = files.some(f => f.status === "processing")
    if (!isProcessing) return

    const interval = setInterval(async () => {
      try {
        const updatedFiles = await getFilesAPI(notebook_id)
        if (Array.isArray(updatedFiles)) setFiles(updatedFiles)
      } catch (err) {
        console.error("Polling error:", err)
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [notebook_id, files])

  async function uploadFile(file) {
    let newFile
    try {
      newFile = await uploadFileAPI(notebook_id, file)
      if (!newFile) return
      setFiles(prev => [...prev, newFile])
    } catch (err) {
      console.error("Failed to upload file:", err)
      return
    }

    try {
      await fetch(`${API}/api/files/${newFile.id}/process`, {
        method: "POST"
      })
    } catch (err) {
      console.error("Failed to process file:", err)
      setFiles(prev =>
        prev.map(f => f.id === newFile.id ? { ...f, status: "error" } : f)
      )
    }
  }

  async function handleUpload(event) {
    const picked = event.target.files
    if (!picked || picked.length === 0) return
    await Promise.all([...picked].map(uploadFile))
    event.target.value = ""
  }

  async function handleDelete(fileId) {
    try {
      await deleteFileAPI(fileId)
      setFiles(prev => prev.filter(f => f.id !== fileId))
    } catch (err) {
      console.error("Failed to delete file:", err)
    }
  }

  return { files, handleUpload, handleDelete }
}