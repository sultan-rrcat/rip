import { useState, useEffect } from 'react'
import type { ChangeEvent } from 'react'
import { v4 as uuidv4 } from 'uuid'
import type { NotebookFile, FilePreview } from '@/types'
import {
  getFilesAPI,
  deleteFileAPI,
  uploadFileAPI,
  processFileAPI,
  getFilePreviewAPI,
  fetchFileBlob,
  ALLOWED_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  MAX_UPLOAD_SIZE_MB,
  extensionOfFile,
} from '@/services/files'
import { ApiError } from '@/services/http'
import { notifyToast } from '@/services/toast'

const ALLOWED = new Set<string>(ALLOWED_EXTENSIONS as unknown as string[])

function friendlyUploadError(err: unknown, fileName: string): string {
  if (err instanceof ApiError) {
    if (err.status === 413)
      return `"${fileName}" exceeds the ${MAX_UPLOAD_SIZE_MB} MB limit.`
    if (err.status === 415)
      return `"${fileName}" type not allowed. Allowed: ${ALLOWED_EXTENSIONS.join(' ')}.`
    if (err.status === 503)
      return `RAG models not loaded — "${fileName}" uploaded but cannot process yet. Retry below.`
    if (err.detail) return `"${fileName}": ${err.detail}`
  }
  if (err instanceof Error && err.message) return `"${fileName}": ${err.message}`
  return `Could not add "${fileName}". Try again.`
}

export function useFiles(notebook_id: string | undefined) {
  const [files, setFiles] = useState<NotebookFile[]>([])
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [previewFile, setPreviewFile] = useState<NotebookFile | null>(null)
  const [preview, setPreview] = useState<FilePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewBlobUrl, setPreviewBlobUrl] = useState<string | null>(null)

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
        notifyToast('Could not load files. Check the backend connection.')
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
    const ext = extensionOfFile(file.name)
    // Client guards mirror the backend (config.py): reject before any
    // network so a 60 MB drop or a .exe never burns an upload.
    if (!ext || !ALLOWED.has(ext)) {
      const msg = `"${file.name}" type not allowed. Allowed: ${ALLOWED_EXTENSIONS.join(' ')}.`
      setUploadError(msg)
      setFiles((prev) => [
        ...prev,
        { id: `temp-${uuidv4()}`, name: file.name, size: file.size, status: 'error', errorDetail: msg },
      ])
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      const msg = `"${file.name}" exceeds the ${MAX_UPLOAD_SIZE_MB} MB limit.`
      setUploadError(msg)
      setFiles((prev) => [
        ...prev,
        { id: `temp-${uuidv4()}`, name: file.name, size: file.size, status: 'error', errorDetail: msg },
      ])
      return
    }

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
      const msg = friendlyUploadError(err, file.name)
      setUploadError(msg)
      notifyToast(msg)
      setFiles((prev) =>
        prev.map((f) => (f.id === tempId ? { ...f, status: 'error', errorDetail: msg } : f)),
      )
      return
    }

    try {
      await processFileAPI(newFile.id)
    } catch (err) {
      console.error('Failed to process file:', err)
      const msg = friendlyUploadError(err, newFile.name)
      setUploadError(msg)
      notifyToast(msg)
      setFiles((prev) =>
        prev.map((f) => (f.id === newFile.id ? { ...f, status: 'error', errorDetail: msg } : f)),
      )
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const picked = event.target.files
    if (!picked || picked.length === 0) return
    setUploadError(null)
    await Promise.all([...picked].map(uploadFile))
    event.target.value = ''
  }

  // Retry a failed file (re-POST /process — the documented recovery path,
  // ADR-005/034). Temp client-guard rows have no server id: drop them so
  // the user re-picks the file instead.
  async function handleRetry(fileId: string): Promise<void> {
    const target = files.find((f) => f.id === fileId)
    if (!target || target.status !== 'error') return
    if (target.id.startsWith('temp-')) {
      setFiles((prev) => prev.filter((f) => f.id !== fileId))
      return
    }
    setUploadError(null)
    setFiles((prev) =>
      prev.map((f) => (f.id === fileId ? { ...f, status: 'processing', errorDetail: undefined } : f)),
    )
    try {
      await processFileAPI(fileId)
    } catch (err) {
      console.error('Failed to re-process file:', err)
      const msg = friendlyUploadError(err, target.name)
      setUploadError(msg)
      notifyToast(msg)
      setFiles((prev) =>
        prev.map((f) => (f.id === fileId ? { ...f, status: 'error', errorDetail: msg } : f)),
      )
    }
  }

  // Re-process a long-stuck `processing` file (restart mid-ingestion can
  // strand it — ADR-005). Same endpoint, harmless when already running.
  async function handleReprocess(fileId: string): Promise<void> {
    const target = files.find((f) => f.id === fileId)
    if (!target || target.id.startsWith('temp-')) return
    try {
      await processFileAPI(fileId)
    } catch (err) {
      console.error('Failed to re-process file:', err)
      const msg = friendlyUploadError(err, target.name)
      setUploadError(msg)
      notifyToast(msg)
      setFiles((prev) =>
        prev.map((f) => (f.id === fileId ? { ...f, status: 'error', errorDetail: msg } : f)),
      )
    }
  }

  function dismissFileError(fileId: string): void {
    setFiles((prev) => prev.filter((f) => f.id !== fileId))
  }

  async function handleDelete(fileId: string): Promise<void> {
    try {
      await deleteFileAPI(fileId)
      setFiles((prev) => prev.filter((f) => f.id !== fileId))
    } catch (err) {
      console.error('Failed to delete file:', err)
      notifyToast('Could not delete the file. Try again.')
    }
  }

  async function openPreview(file: NotebookFile): Promise<void> {
    if (file.status !== 'ready') return
    if (file.id.startsWith('temp-')) return
    // Revoke any previous blob URL before loading the next preview.
    setPreviewBlobUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return null
    })
    setPreviewFile(file)
    setPreview(null)
    setPreviewError(null)
    setPreviewLoading(true)
    try {
      const data = await getFilePreviewAPI(file.id)
      setPreview(data)
      if (data.kind === 'pdf') {
        try {
          const blob = await fetchFileBlob(file.id)
          setPreviewBlobUrl(URL.createObjectURL(blob))
        } catch (err) {
          console.error('Failed to load PDF preview:', err)
          setPreviewError('Failed to load PDF preview.')
          notifyToast('Could not load the PDF preview. Use Download instead.')
        }
      }
    } catch (err) {
      console.error('Failed to load preview:', err)
      setPreviewError('Failed to load preview.')
      notifyToast('Could not load the preview. Use Download instead.')
    } finally {
      setPreviewLoading(false)
    }
  }

  function closePreview(): void {
    setPreviewFile(null)
    setPreview(null)
    setPreviewError(null)
    setPreviewBlobUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return null
    })
  }

  // Revoke the blob URL on unmount so pdf object URLs never leak.
  useEffect(() => {
    return () => {
      setPreviewBlobUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev)
        return null
      })
    }
  }, [])

  return {
    files,
    uploadError,
    dismissUploadError: () => setUploadError(null),
    handleUpload,
    handleDelete,
    handleRetry,
    handleReprocess,
    dismissFileError,
    previewFile,
    preview,
    previewLoading,
    previewError,
    previewBlobUrl,
    openPreview,
    closePreview,
  }
}
