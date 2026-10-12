import { useState, useEffect } from 'react'
import { getNotebooksAPI, renameNotebookAPI } from '@/services/notebooks'
import { notifyToast } from '@/services/toast'

export function useNotebook(notebook_id: string | undefined) {
  const [notebookName, setNotebookName] = useState('')

  useEffect(() => {
    if (!notebook_id) return
    let cancelled = false

    async function loadNotebookName() {
      try {
        const notebooks = await getNotebooksAPI()
        const notebook = notebooks.find((n) => n.notebook_id === notebook_id)
        if (!cancelled && notebook) {
          setNotebookName(notebook.notebook_name)
        }
      } catch (err) {
        console.error('Failed to load notebook name:', err)
        notifyToast('Could not load the notebook name.')
      }
    }

    loadNotebookName()
    return () => {
      cancelled = true
    }
  }, [notebook_id])

  async function renameNotebook(newName: string): Promise<void> {
    if (!notebook_id) return
    if (!newName.trim()) return
    try {
      await renameNotebookAPI(notebook_id, newName)
      setNotebookName(newName)
    } catch (err) {
      console.error('Failed to rename notebook:', err)
      notifyToast('Could not rename the notebook. Try again.')
    }
  }

  return { notebookName, renameNotebook }
}
