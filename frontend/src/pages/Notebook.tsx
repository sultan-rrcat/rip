import { useParams, Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import DownloadIcon from '@mui/icons-material/Download'
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep'
import MenuIcon from '@mui/icons-material/Menu'
import TuneIcon from '@mui/icons-material/Tune'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import ChatArea from '@/components/notebook/ChatArea'
import Footer from '@/components/notebook/Footer'
import LeftSidebar from '@/components/notebook/LeftSidebar'
import RightStudio from '@/components/notebook/RightStudio'
import FilePreviewModal from '@/components/notebook/FilePreviewModal'
import ToastHost from '@/components/ToastHost'
import { API } from '@/config'
import { useNotebook } from '@/hooks/notebooks/useNotebook'
import { useFiles } from '@/hooks/notebooks/useFiles'
import { useMessages } from '@/hooks/notebooks/useMessages'
import { useStudio } from '@/hooks/notebooks/useStudio'
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

export default function Notebook() {
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
  // Both side panels visible on first paint at desktop widths; closed on
  // small screens where they are slide-over drawers. Initialized from the
  // viewport once (not a live media query) so a resize never yanks panels.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => typeof window === 'undefined' || window.innerWidth >= 768,
  )
  const [studioOpen, setStudioOpen] = useState(
    () => typeof window !== 'undefined' && window.innerWidth >= 1024,
  )
  const [backendUp, setBackendUp] = useState<boolean | null>(null)
  const studio = useStudio(notebook_id)

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

  // Keyboard: `/` focuses the composer, `Esc` closes the side drawers.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      const typing =
        target != null &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable)
      if (e.key === '/' && !typing) {
        e.preventDefault()
        document
          .querySelector<HTMLTextAreaElement>(
            'textarea[aria-label="Ask about your sources"]',
          )
          ?.focus()
      }
      if (e.key === 'Escape') {
        setSidebarOpen(false)
        setStudioOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
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
          className="backdrop-enter fixed inset-0 z-30 bg-ink/40 md:hidden"
        />
      )}
      <div
        className={
          sidebarOpen
            ? 'sidebar-enter fixed inset-y-0 left-0 z-40 w-[300px] max-w-[85vw] p-2 md:static md:z-auto md:block md:w-auto md:max-w-none md:p-0'
            : 'hidden'
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
              title="Sources — knowledge base"
              className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-paper hover:text-ink ${sidebarOpen ? 'bg-paper text-ink' : 'text-ink-soft/70'}`}
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
              className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[12px] font-semibold text-ink-soft/70 transition-colors hover:bg-paper hover:text-ink disabled:cursor-not-allowed disabled:opacity-30"
            >
              <DownloadIcon sx={{ fontSize: 17 }} />
              Export
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
            <button
              type="button"
              onClick={() => setStudioOpen((v) => !v)}
              aria-label={studioOpen ? 'Hide studio panel' : 'Show studio panel'}
              aria-expanded={studioOpen}
              title="Studio — one-click quiz & convert"
              className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-paper hover:text-ink ${studioOpen ? 'bg-paper text-ink' : 'text-ink-soft/70'}`}
            >
              <TuneIcon sx={{ fontSize: 17 }} />
            </button>
            <p className="font-ledger ml-2 hidden text-[10px] tracking-[0.2em] text-ink-soft/50 lg:block">
              NOTHING LEAVES THIS NETWORK
            </p>
          </div>
        </div>
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
          />
        </div>
        <Footer
          onSendMessage={handleSendMessage}
          isLoading={isRunning}
          isRunning={isRunning}
          onCancel={handleCancelRun}
        />
      </main>

      {/* Studio panel: static column on lg+, slide-over below it */}
      {studioOpen && (
        <button
          type="button"
          aria-label="Close studio panel"
          onClick={() => setStudioOpen(false)}
          className="backdrop-enter fixed inset-0 z-30 bg-ink/40 lg:hidden"
        />
      )}
      <div
        className={
          studioOpen
            ? 'sidebar-enter fixed inset-y-0 right-0 z-40 w-[336px] max-w-[90vw] p-2 lg:static lg:z-auto lg:block lg:w-auto lg:max-w-none lg:p-0'
            : 'hidden'
        }
      >
        {studioOpen && (
          <div className="h-full lg:h-full">
            <RightStudio
              files={files}
              jobs={studio.jobs}
              loading={studio.loading}
              error={studio.error}
              starting={studio.starting}
              onStartQuiz={studio.startQuizJob}
              onStartConvert={studio.startConvertJob}
              onCancel={(jobId) => void studio.cancelJob(jobId)}
              onReload={() => void studio.reload()}
            />
          </div>
        )}
      </div>

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
