import { useParams, Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import DownloadIcon from '@mui/icons-material/Download'
import RefreshIcon from '@mui/icons-material/Refresh'
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep'
import MenuIcon from '@mui/icons-material/Menu'
import SettingsIcon from '@mui/icons-material/Settings'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import ChatArea from '@/components/notebook/ChatArea'
import Footer from '@/components/notebook/Footer'
import LeftSidebar from '@/components/notebook/LeftSidebar'
import FilePreviewModal from '@/components/notebook/FilePreviewModal'
import RunStatusBar from '@/components/notebook/RunStatusBar'
import LogoutButton from '@/components/LogoutButton'
import ToastHost from '@/components/ToastHost'
import { API } from '@/config'
import { useNotebook } from '@/hooks/notebooks/useNotebook'
import { useFiles } from '@/hooks/notebooks/useFiles'
import { useMessages } from '@/hooks/notebooks/useMessages'
import type { Message, Source } from '@/types'

function messagesToMarkdown(messages: Message[], notebookName: string): string {
  const lines: string[] = [`# ${notebookName || 'Notebook'}`, '']
  for (const m of messages) {
    if (m.role === 'user') {
      lines.push(`## You`, '', m.text, '')
    } else if (m.role === 'assistant') {
      lines.push(`## Assistant`, '', m.text || '(empty response)', '')
      if (m.sources && m.sources.length > 0) {
        lines.push('**Sources:**')
        for (const s of m.sources) lines.push(`- ${s.source} — ${s.section}`)
        lines.push('')
      }
      if (m.artifacts && m.artifacts.length > 0) {
        lines.push('**Artifacts:**')
        for (const a of m.artifacts) lines.push(`- ${a.filename} (${a.kind})`)
        lines.push('')
      }
    } else {
      lines.push(`## Error`, '', m.text, '')
    }
  }
  return lines.join('\n')
}

function downloadMarkdown(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export default function Notebook({ isAdmin = false }: { isAdmin?: boolean }) {
  const { notebook_id } = useParams()
  const { notebookName, renameNotebook } = useNotebook(notebook_id)
  const {
    files,
    uploadError,
    dismissUploadError,
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
  } = useFiles(notebook_id)
  const {
    messages,
    activeRun,
    pastRuns,
    isRunning,
    cancelledIds,
    handleSendMessage,
    handleEditMessage,
    handleCancelRun,
    handleRegenerate,
    handleClearChat,
    canRegenerate,
  } = useMessages(notebook_id)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [backendUp, setBackendUp] = useState<boolean | null>(null)

  // Backend health dot (public endpoint, no auth). One probe on mount;
  // gray = unknown/checking, green = up, red = down.
  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/health`, { credentials: 'include' })
      .then((res) => {
        if (!cancelled) setBackendUp(res.ok)
      })
      .catch(() => {
        if (!cancelled) setBackendUp(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  function handleSourceClick(source: Source) {
    const match = files.find(
      (f) => f.name === source.source || f.name.toLowerCase() === source.source.toLowerCase(),
    )
    if (match) void openPreview(match)
  }

  function handleExport() {
    const md = messagesToMarkdown(messages, notebookName)
    const safe = (notebookName || 'notebook').replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '-').toLowerCase() || 'notebook'
    downloadMarkdown(`${safe}-chat.md`, md)
  }

  async function handleConfirmClear() {
    setClearing(true)
    setClearError(null)
    try {
      await handleClearChat()
      setConfirmClear(false)
    } catch {
      setClearError('Could not clear the chat. Try again.')
    } finally {
      setClearing(false)
    }
  }

  const hasConversation = messages.some((m) => m.role === 'user')

  return (
    <div className="flex h-screen min-h-0 gap-2 overflow-hidden bg-bench p-2">
      {/* Mobile backdrop for the drawer */}
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close sources panel"
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-ink/40 md:hidden"
        />
      )}
      <div
        className={
          sidebarOpen
            ? 'fixed inset-y-0 left-0 z-40 w-[300px] max-w-[85vw] p-2 md:static md:z-auto md:block md:w-auto md:max-w-none md:p-0'
            : 'hidden md:block'
        }
      >
        <div className="h-full md:h-full">
          <LeftSidebar
            files={files}
            uploadError={uploadError}
            onDismissUploadError={dismissUploadError}
            onUpload={handleUpload}
            onDelete={handleDelete}
            onRetry={handleRetry}
            onReprocess={handleReprocess}
            onDismissFileError={dismissFileError}
            onPreview={(f) => {
              setSidebarOpen(false)
              openPreview(f)
            }}
            notebookName={notebookName}
            onRenameNotebook={renameNotebook}
          />
        </div>
      </div>
      <FilePreviewModal
        file={previewFile}
        preview={preview}
        loading={previewLoading}
        error={previewError}
        blobUrl={previewBlobUrl}
        onClose={closePreview}
      />
      <ToastHost />
      <main className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
        <div className="flex shrink-0 items-center justify-between gap-2 px-1 pt-0.5">
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setSidebarOpen((v) => !v)}
              aria-label={sidebarOpen ? 'Hide sources panel' : 'Show sources panel'}
              aria-expanded={sidebarOpen}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-soft/70 transition-colors hover:bg-paper hover:text-ink md:hidden"
            >
              <MenuIcon sx={{ fontSize: 18 }} />
            </button>
            <Link
              to="/"
              className="font-ledger text-[11px] font-semibold tracking-[0.18em] text-ink-soft/70 transition-colors hover:text-ink"
            >
              ← STACK
            </Link>
          </div>
          <div className="flex items-center gap-1">
            <span
              title={
                backendUp === null
                  ? 'Checking backend…'
                  : backendUp
                    ? 'Backend reachable'
                    : 'Backend unreachable — check the server'
              }
              aria-label={
                backendUp === null
                  ? 'Backend status unknown'
                  : backendUp
                    ? 'Backend reachable'
                    : 'Backend unreachable'
              }
              className={`inline-block h-2 w-2 rounded-full ${
                backendUp === null ? 'bg-line' : backendUp ? 'bg-emerald-500' : 'bg-rust'
              }`}
            />
            <button
              type="button"
              onClick={handleExport}
              disabled={!hasConversation}
              title="Export chat as Markdown"
              aria-label="Export chat as Markdown"
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-soft/70 transition-colors hover:bg-paper hover:text-ink disabled:cursor-not-allowed disabled:opacity-30"
            >
              <DownloadIcon sx={{ fontSize: 17 }} />
            </button>
            <button
              type="button"
              onClick={() => void handleRegenerate()}
              disabled={!canRegenerate || isRunning}
              title="Regenerate last answer"
              aria-label="Regenerate last answer"
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-soft/70 transition-colors hover:bg-paper hover:text-ink disabled:cursor-not-allowed disabled:opacity-30"
            >
              <RefreshIcon sx={{ fontSize: 17 }} />
            </button>
            <button
              type="button"
              onClick={() => {
                setClearError(null)
                setConfirmClear(true)
              }}
              disabled={!hasConversation || isRunning}
              title="Clear chat history"
              aria-label="Clear chat history"
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-soft/70 transition-colors hover:bg-rust/10 hover:text-rust disabled:cursor-not-allowed disabled:opacity-30"
            >
              <DeleteSweepIcon sx={{ fontSize: 17 }} />
            </button>
            {isAdmin && (
              <Link
                to="/admin"
                title="Runtime config"
                aria-label="Open admin runtime config"
                className="hidden h-8 w-8 items-center justify-center rounded-md text-ink-soft/70 transition-colors hover:bg-paper hover:text-ink sm:flex"
              >
                <SettingsIcon sx={{ fontSize: 17 }} />
              </Link>
            )}
            <span className="[&_button]:text-ink-soft/70">
              <LogoutButton />
            </span>
            <p className="font-ledger ml-2 hidden text-[10px] tracking-[0.2em] text-ink-soft/50 lg:block">
              OFFLINE · NOTHING LEAVES THIS MACHINE
            </p>
          </div>
        </div>
        {isRunning && activeRun && <RunStatusBar run={activeRun} />}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <ChatArea
            messages={messages}
            activeRun={activeRun}
            pastRuns={pastRuns}
            isRunning={isRunning}
            cancelledIds={cancelledIds}
            canRegenerate={canRegenerate}
            onEditMessage={handleEditMessage}
            onRegenerate={() => void handleRegenerate()}
            onSourceClick={handleSourceClick}
            onSendMessage={handleSendMessage}
          />
        </div>
        <Footer
          onSendMessage={handleSendMessage}
          isLoading={isRunning}
          isRunning={isRunning}
          onCancel={handleCancelRun}
        />
      </main>

      <Dialog open={confirmClear} onClose={() => !clearing && setConfirmClear(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Clear chat history?</DialogTitle>
        <DialogContent>
          <p className="text-[13px] leading-relaxed">
            This deletes every message in this notebook. Files stay untouched.
            This cannot be undone.
          </p>
          {clearError && (
            <p role="alert" className="mt-2 text-[13px] text-rust">
              {clearError}
            </p>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmClear(false)} disabled={clearing}>
            Keep
          </Button>
          <Button onClick={handleConfirmClear} color="error" variant="contained" disabled={clearing}>
            {clearing ? 'Clearing…' : 'Clear all'}
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  )
}
