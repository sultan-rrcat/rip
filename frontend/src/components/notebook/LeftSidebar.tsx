import AddIcon from '@mui/icons-material/Add'
import LocalLibraryIcon from '@mui/icons-material/LocalLibrary'
import FolderOpenIcon from '@mui/icons-material/FolderOpen'
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf'
import DescriptionIcon from '@mui/icons-material/Description'
import TextSnippetIcon from '@mui/icons-material/TextSnippet'
import TableChartIcon from '@mui/icons-material/TableChart'
import CodeIcon from '@mui/icons-material/Code'
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile'
import DeleteIcon from '@mui/icons-material/Delete'
import RefreshIcon from '@mui/icons-material/Refresh'
import CloseIcon from '@mui/icons-material/Close'
import { useState, memo } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'
import type { NotebookFile } from '@/types'
import { FILE_ACCEPT } from '@/services/files'

interface LeftSidebarProps {
  files: NotebookFile[]
  uploadError: string | null
  onDismissUploadError: () => void
  onUpload: (event: ChangeEvent<HTMLInputElement>) => void
  onDelete: (fileId: string) => void
  onRetry: (fileId: string) => void
  onDismissFileError: (fileId: string) => void
  onPreview: (file: NotebookFile) => void
  onRenameNotebook: (name: string) => void
  notebookName: string
}

const LeftSidebar = memo(function LeftSidebar({
  files,
  uploadError,
  onDismissUploadError,
  onUpload,
  onDelete,
  onRetry,
  onDismissFileError,
  onPreview,
  onRenameNotebook,
  notebookName,
}: LeftSidebarProps) {
  const readyCount = files.filter((f) => f.status === 'ready').length
  const [isRenaming, setIsRenaming] = useState(false)
  const [tempName, setTempName] = useState(notebookName)

  function handleRenameSave() {
    if (!tempName.trim()) return
    onRenameNotebook(tempName)
    setIsRenaming(false)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') handleRenameSave()
    if (e.key === 'Escape') {
      setTempName(notebookName)
      setIsRenaming(false)
    }
  }

  return (
    <div className="flex h-full w-[292px] shrink-0 flex-col gap-2">
      {/* drawer front: notebook name + brass pull */}
      <div className="rounded-lg border border-line bg-card px-3 pb-2.5 pt-2 shadow-[0_1px_0_rgba(21,39,54,0.12)]">
        <div className="flex justify-center pb-1.5" aria-hidden="true">
          <span className="block h-1.5 w-12 rounded-full bg-brass" />
        </div>
        {isRenaming ? (
          <input
            autoFocus
            value={tempName}
            onChange={(e) => setTempName(e.target.value)}
            onBlur={handleRenameSave}
            onKeyDown={handleKeyDown}
            aria-label="Notebook name"
            className="font-display w-full rounded border border-ledger/50 bg-paper px-2 py-1 text-[15px] font-semibold text-ink outline-none"
          />
        ) : (
          <button
            type="button"
            onClick={() => {
              setTempName(notebookName)
              setIsRenaming(true)
            }}
            title="Rename notebook"
            className="font-display w-full cursor-pointer truncate rounded px-2 py-1 text-left text-[15px] font-semibold text-ink transition-colors hover:bg-paper"
          >
            {notebookName}
          </button>
        )}
      </div>
      {/* source cabinet */}
      <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-card shadow-[0_1px_0_rgba(21,39,54,0.12)]">
        <aside className="flex min-h-0 w-full flex-1 flex-col">
          {/* header  */}
          <div className="m-3 mb-0 flex items-center justify-between rounded-md border border-line/70 bg-paper p-3">
            <div>
              <h1 className="font-display text-[15px] font-bold leading-tight text-ink">
                Knowledge Base
              </h1>
              <p className="font-ledger mt-1 text-[10px] tracking-[0.14em] text-ink-soft/70">
                {readyCount} ACTIVE SOURCE{readyCount !== 1 ? 'S' : ''}
              </p>
            </div>
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-ink text-paper">
              <LocalLibraryIcon fontSize="small" />
            </div>
          </div>

          {/* upload button  */}
          <div className="px-3 pt-3">
            <input
              id="file-upload"
              type="file"
              accept={FILE_ACCEPT}
              multiple
              className="hidden"
              onChange={onUpload}
            />

            <label
              htmlFor="file-upload"
              className="flex w-full cursor-pointer items-center justify-center gap-2 rounded-md bg-ink py-2 text-sm font-semibold text-paper transition-colors hover:bg-ink-soft"
            >
              <AddIcon fontSize="small" />
              Add sources
            </label>
            {uploadError && (
              <div role="alert" className="mt-2 flex items-start gap-2 rounded-md border border-rust/50 bg-rust/[0.07] px-2.5 py-2 text-[11px] leading-snug text-rust">
                <span className="min-w-0 flex-1">{uploadError}</span>
                <button
                  type="button"
                  onClick={onDismissUploadError}
                  aria-label="Dismiss upload error"
                  className="rounded p-0.5 hover:bg-rust/10"
                >
                  <CloseIcon sx={{ fontSize: 14 }} />
                </button>
              </div>
            )}
          </div>

          {/* file list  */}
          <nav
            className="m-3 flex-1 overflow-y-auto rounded-md border border-line/70 bg-paper/50 px-2 py-2"
            aria-label="Sources"
          >
            {files.length === 0 && (
              <div className="px-4 py-8 text-center text-ink-soft/60">
                <FolderOpenIcon />
                <p className="mt-2 text-xs">No files uploaded yet</p>
                <p className="font-ledger mt-1 text-[10px] tracking-wide">
                  PDF · DOC · DOCX · MD · TXT · CSV · CODE
                </p>
                <p className="font-ledger mt-1 text-[9px] tracking-wide text-ink-soft/50">
                  MAX 50 MB PER FILE
                </p>
              </div>
            )}
            <div className="space-y-1.5">
              {files.map((file) => (
                <FileItem
                  key={file.id}
                  file={file}
                  onDelete={onDelete}
                  onRetry={onRetry}
                  onDismissFileError={onDismissFileError}
                  onPreview={onPreview}
                />
              ))}
            </div>
          </nav>
        </aside>
      </div>
    </div>
  )
})

export default LeftSidebar

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

// Mirrors backend code_extensions (backend/app/core/config.py:125).
// Any file with one of these gets the code icon, not the generic one.
const CODE_EXTENSIONS = new Set([
  'py', 'js', 'ts', 'jsx', 'tsx', 'java', 'c', 'cpp', 'h', 'hpp',
  'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'r',
  'm', 'sh', 'ps1', 'sql', 'html', 'css', 'scss', 'less',
  'json', 'xml', 'yaml', 'yml', 'toml', 'ini',
])

function FileTypeIcon({ name }: { name: string }) {
  const ext = fileExtension(name)
  const cls = 'shrink-0'
  if (ext === 'pdf')
    return (
      <PictureAsPdfIcon fontSize="small" className={`${cls} text-rust`} aria-hidden="true" />
    )
  if (ext === 'doc' || ext === 'docx')
    return (
      <DescriptionIcon fontSize="small" className={`${cls} text-[#2b579a]`} aria-hidden="true" />
    )
  if (ext === 'md' || ext === 'markdown')
    return (
      <TextSnippetIcon fontSize="small" className={`${cls} text-ink-soft`} aria-hidden="true" />
    )
  if (ext === 'txt')
    return (
      <TextSnippetIcon fontSize="small" className={`${cls} text-ink-soft/70`} aria-hidden="true" />
    )
  if (ext === 'csv')
    return (
      <TableChartIcon fontSize="small" className={`${cls} text-ledger`} aria-hidden="true" />
    )
  if (CODE_EXTENSIONS.has(ext))
    return (
      <CodeIcon fontSize="small" className={`${cls} text-ledger`} aria-hidden="true" />
    )
  return (
    <InsertDriveFileIcon fontSize="small" className={`${cls} text-ink-soft/60`} aria-hidden="true" />
  )
}

interface FileItemProps {
  file: NotebookFile
  onDelete: (fileId: string) => void
  onRetry: (fileId: string) => void
  onDismissFileError: (fileId: string) => void
  onPreview: (file: NotebookFile) => void
}

function FileItem({ file, onDelete, onRetry, onDismissFileError, onPreview }: FileItemProps) {
  const [confirming, setConfirming] = useState(false)
  const tone = {
    uploading: 'border-brass/50 bg-brass/10',
    processing: 'border-brass/50 bg-brass/10',
    ready: 'border-ledger/40 bg-ledger/[0.07]',
    error: 'border-rust/50 bg-rust/[0.07]',
  }[file.status]
  const canPreview = file.status === 'ready' && !file.id.startsWith('temp-')
  const isTempError = file.status === 'error' && file.id.startsWith('temp-')
  return (
    <div className={`rounded-md border ${tone}`}>
      <div
        className={`group flex items-center justify-between gap-2 px-2.5 py-2 ${canPreview ? 'cursor-pointer' : ''}`}
        onClick={() => {
          if (canPreview) onPreview(file)
        }}
        role={canPreview ? 'button' : undefined}
        tabIndex={canPreview ? 0 : undefined}
        title={canPreview ? `Preview ${file.name}` : file.name}
        onKeyDown={(e) => {
          if (canPreview && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault()
            onPreview(file)
          }
        }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2.5 overflow-hidden">
          <FileTypeIcon name={file.name} />
          <div className="min-w-0">
            <p
              className="truncate text-[11px] font-medium text-ink"
              title={file.name}
            >
              {file.name}
            </p>
            <p className="font-ledger text-[9px] tracking-wide text-ink-soft/60">
              {formatFileSize(file.size)}
            </p>
            <p className="font-ledger flex items-center gap-1.5 text-[9px] tracking-wide">
              {file.status === 'uploading' && (
                <>
                  <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-line border-t-brass-deep"></span>
                  <span className="text-brass-deep">UPLOADING</span>
                  <span className="process-bar mt-1 block h-1 w-full rounded-full bg-brass/20" aria-hidden="true" />
                </>
              )}

              {file.status === 'processing' && (
                <>
                  <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-line border-t-brass-deep"></span>
                  <span className="text-brass-deep">PROCESSING</span>
                  <span className="process-bar mt-1 block h-1 w-full rounded-full bg-brass/20" aria-hidden="true" />
                </>
              )}

              {file.status === 'ready' && (
                <span className="font-semibold text-ledger">READY</span>
              )}

              {file.status === 'error' && (
                <span className="font-medium text-rust">
                  ERROR ADDING FILE
                </span>
              )}
            </p>
            {file.status === 'error' && file.errorDetail && (
              <p className="mt-1 break-words text-[10px] leading-snug text-rust/90" title={file.errorDetail}>
                {file.errorDetail}
              </p>
            )}
            {file.status === 'error' && !isTempError && (
              <div className="mt-1.5 flex items-center gap-2">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    onRetry(file.id)
                  }}
                  title="Retry processing"
                  aria-label={`Retry processing ${file.name}`}
                  className="font-ledger inline-flex items-center gap-1 rounded border border-rust/40 bg-card px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-rust hover:bg-rust/10"
                >
                  <RefreshIcon sx={{ fontSize: 12 }} />
                  RETRY
                </button>
              </div>
            )}
            {isTempError && (
              <div className="mt-1.5">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    onDismissFileError(file.id)
                  }}
                  className="font-ledger text-[9px] tracking-wide text-ink-soft/70 underline hover:text-ink"
                >
                  DISMISS
                </button>
              </div>
            )}
          </div>
        </div>

        {confirming ? (
          <div className="flex items-center justify-between gap-2 border-t border-rust/30 px-2.5 py-1.5">
            <span className="font-ledger text-[9px] font-semibold tracking-wide text-rust">
              DELETE?
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  setConfirming(false)
                }}
                className="font-ledger rounded px-1.5 py-0.5 text-[9px] text-ink-soft hover:bg-paper"
              >
                KEEP
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(file.id)
                }}
                aria-label={`Confirm delete ${file.name}`}
                className="font-ledger rounded bg-rust px-1.5 py-0.5 text-[9px] font-semibold text-white hover:bg-rust/85"
              >
                YES
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={(e) => {
              e.stopPropagation()
              setConfirming(true)
            }}
            className="rounded p-1 opacity-0 transition-opacity duration-200 hover:bg-rust/10 focus:opacity-100 group-hover:opacity-100"
            aria-label={`Delete ${file.name}`}
          >
            <DeleteIcon
              fontSize="small"
              className="text-ink-soft/60 hover:text-rust"
            />
          </button>
        )}
      </div>
    </div>
  )
}
