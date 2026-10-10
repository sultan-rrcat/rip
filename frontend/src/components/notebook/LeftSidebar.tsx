import AddIcon from '@mui/icons-material/Add'
import LocalLibraryIcon from '@mui/icons-material/LocalLibrary'
import FolderOpenIcon from '@mui/icons-material/FolderOpen'
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf'
import DescriptionIcon from '@mui/icons-material/Description'
import TextSnippetIcon from '@mui/icons-material/TextSnippet'
import CodeIcon from '@mui/icons-material/Code'
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile'
import DeleteIcon from '@mui/icons-material/Delete'
import { useState, memo } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'
import type { NotebookFile } from '@/types'

interface LeftSidebarProps {
  files: NotebookFile[]
  onUpload: (event: ChangeEvent<HTMLInputElement>) => void
  onDelete: (fileId: string) => void
  onRenameNotebook: (name: string) => void
  notebookName: string
}

const LeftSidebar = memo(function LeftSidebar({
  files,
  onUpload,
  onDelete,
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
              accept=".pdf,.doc,.docx,.md,.txt,.py"
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
                  PDF · DOC · MD · TXT · PY
                </p>
              </div>
            )}
            <div className="space-y-1.5">
              {files.map((file) => (
                <FileItem key={file.id} file={file} onDelete={onDelete} />
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
}

function FileItem({ file, onDelete }: FileItemProps) {
  const tone = {
    uploading: 'border-brass/50 bg-brass/10',
    processing: 'border-brass/50 bg-brass/10',
    ready: 'border-ledger/40 bg-ledger/[0.07]',
    error: 'border-rust/50 bg-rust/[0.07]',
  }[file.status]
  return (
    <div className={`rounded-md border ${tone}`}>
      <div className="group flex cursor-pointer items-center justify-between gap-2 px-2.5 py-2">
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
                </>
              )}

              {file.status === 'processing' && (
                <>
                  <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-line border-t-brass-deep"></span>
                  <span className="text-brass-deep">PROCESSING</span>
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
          </div>
        </div>

        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete(file.id)
          }}
          className="rounded p-1 opacity-0 transition-opacity duration-200 hover:bg-rust/10 focus:opacity-100 group-hover:opacity-100"
          aria-label={`Delete ${file.name}`}
        >
          <DeleteIcon
            fontSize="small"
            className="text-ink-soft/60 hover:text-rust"
          />
        </button>
      </div>
    </div>
  )
}
