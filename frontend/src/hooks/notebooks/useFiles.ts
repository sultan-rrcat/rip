import { useState, useEffect } from 'react'
import type { ChangeEvent } from 'react'
import { v4 as uuidv4 } from 'uuid'
import type { NotebookFile } from '@/types'
import {
  getFilesAPI,
  deleteFileAPI,
  uploadFileAPI,
  processFileAPI,
} from '@/services/files'

export function useFiles(notebook_id: string | undefined) {
  const [files, setFiles] = useState<NotebookFile[]>([])

  // Initial fetch — guard against undefined id (first render race)
  useEffect(() => {
    if (!notebook_id) return
    const id = notebook_id
    let cancelled = false

    async function loadFiles() {
      try {
        const fetchedFiles = await getFilesAPI(id)
        // Guard: API might return an error object instead of an array
        if (!cancelled)
          setFiles(Array.isArray(fetchedFiles) ? fetchedFiles : [])
      } catch (err) {
        console.error('Failed to load files:', err)
      }
    }

    loadFiles()
    return () => {
      cancelled = true
    }
  }, [notebook_id])

  // Poll while any file is still processing. Keyed on the derived boolean
  // (not the array) so a poll response doesn't tear down and recreate the
  // interval on every tick.
  const hasProcessing = files.some((f) => f.status === 'processing')
  useEffect(() => {
    if (!notebook_id || !hasProcessing) return
    const id = notebook_id

    const interval = setInterval(async () => {
      try {
        const updatedFiles = await getFilesAPI(id)
        if (Array.isArray(updatedFiles)) setFiles(updatedFiles)
      } catch (err) {
        console.error('Polling error:', err)
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [notebook_id, hasProcessing])

  async function uploadFile(file: File): Promise<void> {
    if (!notebook_id) return
    const tempId = `temp-${uuidv4()}`
    setFiles((prev) => [
      ...prev,
      { id: tempId, name: file.name, size: file.size, status: 'uploading' },
    ])

    let newFile: NotebookFile
    try {
      newFile = await uploadFileAPI(notebook_id, file)
      setFiles((prev) => prev.map((f) => (f.id === tempId ? newFile : f)))
    } catch (err) {
      console.error('Failed to upload file:', err)
      setFiles((prev) =>
        prev.map((f) => (f.id === tempId ? { ...f, status: 'error' } : f)),
      )
      return
    }

    try {
      await processFileAPI(newFile.id)
    } catch (err) {
      console.error('Failed to process file:', err)
      setFiles((prev) =>
        prev.map((f) => (f.id === newFile.id ? { ...f, status: 'error' } : f)),
      )
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const picked = event.target.files
    if (!picked || picked.length === 0) return
    await Promise.all([...picked].map(uploadFile))
    event.target.value = ''
  }

  async function handleDelete(fileId: string): Promise<void> {
    try {
      await deleteFileAPI(fileId)
      setFiles((prev) => prev.filter((f) => f.id !== fileId))
    } catch (err) {
      console.error('Failed to delete file:', err)
    }
  }

  return { files, handleUpload, handleDelete }
}
