import DownloadIcon from '@mui/icons-material/Download'
import CloseIcon from '@mui/icons-material/Close'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import CheckIcon from '@mui/icons-material/Check'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { NotebookFile, FilePreview } from '@/types'
import { fileDownloadUrl } from '@/services/files'

interface FilePreviewModalProps {
  file: NotebookFile | null
  preview: FilePreview | null
  loading: boolean
  error: string | null
  blobUrl: string | null
  onClose: () => void
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

function languageOf(ext: string): string {
  if (ext === 'md' || ext === 'markdown') return 'markdown'
  if (ext === 'py') return 'python'
  if (ext === 'js' || ext === 'jsx') return 'javascript'
  if (ext === 'ts' || ext === 'tsx') return 'typescript'
  if (ext === 'sh') return 'bash'
  if (ext === 'yml' || ext === 'yaml') return 'yaml'
  if (ext === 'html' || ext === 'xml') return 'markup'
  if (['c', 'cpp', 'h', 'hpp', 'cs', 'java', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'r', 'm', 'ps1', 'sql', 'css', 'scss', 'less', 'json', 'toml', 'ini'].includes(ext))
    return ext
  return 'text'
}

export default function FilePreviewModal({
  file,
  preview,
  loading,
  error,
  blobUrl,
  onClose,
}: FilePreviewModalProps) {
  const open = file !== null
  const ext = file ? extensionOf(file.name) : ''
  const isMarkdown = ext === 'md' || ext === 'markdown'
  const isCsv = ext === 'csv'
  const [copiedFor, setCopiedFor] = useState<string | null>(null)
  const copyTimer = useRef<number | null>(null)
  const copied = copiedFor !== null && copiedFor === file?.id

  useEffect(() => {
    return () => {
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
    }
  }, [])

  async function handleCopy() {
    const content = preview?.content ?? ''
    if (!content) return
    try {
      await navigator.clipboard.writeText(content)
    } catch {
      return
    }
    setCopiedFor(file?.id ?? null)
    if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
    copyTimer.current = window.setTimeout(() => setCopiedFor(null), 1600)
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <span
          style={{
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={file?.name ?? ''}
        >
          {file?.name ?? 'Preview'}
        </span>
        <Button
          size="small"
          startIcon={<DownloadIcon fontSize="small" />}
          component="a"
          href={file ? fileDownloadUrl(file.id) : ''}
          download={file?.name}
          disabled={!file}
        >
          Download
        </Button>
        <Button
          size="small"
          onClick={onClose}
          aria-label="Close preview"
          sx={{ minWidth: 0 }}
        >
          <CloseIcon fontSize="small" />
        </Button>
      </DialogTitle>
      <DialogContent dividers sx={{ minHeight: 240 }}>
        {loading && <p className="text-sm text-ink-soft">Loading preview…</p>}
        {!loading && error && <p className="text-sm text-rust">{error}</p>}
        {!loading && !error && preview?.kind === 'text' && (
          <div className="space-y-2">
            {preview.truncated && (
              <p className="font-ledger text-[10px] tracking-wide text-ink-soft/70">
                TRUNCATED TO FIRST 200 KB — DOWNLOAD FOR FULL FILE
              </p>
            )}
            <div className="flex justify-end">
              <Button
                size="small"
                startIcon={copied ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
                onClick={handleCopy}
                disabled={!preview.content}
                aria-label={copied ? 'Copied to clipboard' : 'Copy preview text'}
              >
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
            {isCsv ? (
              <CsvTable content={preview.content ?? ''} />
            ) : isMarkdown ? (
              <div className="prose prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {preview.content ?? ''}
                </ReactMarkdown>
              </div>
            ) : (
              <SyntaxHighlighter
                style={oneLight as { [key: string]: CSSProperties }}
                language={languageOf(ext)}
                PreTag="div"
                customStyle={{ margin: 0, fontSize: '12px' }}
              >
                {preview.content ?? ''}
              </SyntaxHighlighter>
            )}
          </div>
        )}
        {!loading && !error && preview?.kind === 'pdf' && (
          blobUrl ? (
            <iframe
              src={blobUrl}
              title={file?.name ?? 'PDF preview'}
              className="h-[70vh] w-full rounded border border-line bg-white"
            />
          ) : (
            <p className="text-sm text-ink-soft">Loading PDF…</p>
          )
        )}
        {!loading && !error && preview?.kind === 'unsupported' && (
          <div className="space-y-2 text-sm text-ink-soft">
            {ext === 'docx' || ext === 'doc' ? (
              <p>Word preview isn't available — use Download to view it.</p>
            ) : (
              <>
                <p>Preview not available for this file type.</p>
                <p>Use Download to open it locally.</p>
              </>
            )}
          </div>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

// Minimal CSV reader for preview: quoted fields + embedded commas.
// Capped (rows/cols/cell length) so a wide dump never breaks the modal.
const CSV_MAX_ROWS = 50
const CSV_MAX_COLS = 12
const CSV_MAX_CELL = 200

function parseCsvRow(line: string): string[] {
  const cells: string[] = []
  let cur = ''
  let quoted = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (quoted) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"'
          i++
        } else {
          quoted = false
        }
      } else {
        cur += ch
      }
    } else if (ch === '"') {
      quoted = true
    } else if (ch === ',') {
      cells.push(cur)
      cur = ''
    } else {
      cur += ch
    }
  }
  cells.push(cur)
  return cells
}

function CsvTable({ content }: { content: string }) {
  const lines = content.split(/\r?\n/).filter((l) => l.length > 0)
  if (lines.length === 0) {
    return <p className="text-sm text-ink-soft">Empty CSV file.</p>
  }
  const rows = lines.slice(0, CSV_MAX_ROWS + 1).map(parseCsvRow)
  const [header, ...body] = rows
  const cols = Math.min(header.length, CSV_MAX_COLS)
  return (
    <div className="space-y-1">
      <p className="font-ledger text-[10px] tracking-wide text-ink-soft/70">
        {lines.length > CSV_MAX_ROWS + 1
          ? `FIRST ${CSV_MAX_ROWS} ROWS — DOWNLOAD FOR FULL FILE`
          : `${body.length} ROW${body.length === 1 ? '' : 'S'} · ${cols} COL${cols === 1 ? '' : 'S'}`}
      </p>
      <div className="overflow-auto rounded border border-line" style={{ maxHeight: 420 }}>
        <table className="border-collapse text-[12px]">
          <thead className="sticky top-0 bg-paper">
            <tr>
              {header.slice(0, cols).map((h, i) => (
                <th key={i} className="font-ledger border border-line bg-paper px-2 py-1.5 text-left text-[10px] tracking-wide text-ink">
                  {h.slice(0, CSV_MAX_CELL)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, ri) => (
              <tr key={ri} className={ri % 2 === 1 ? 'bg-paper/60' : undefined}>
                {row.slice(0, cols).map((c, ci) => (
                  <td key={ci} className="max-w-56 truncate border border-line px-2 py-1 text-ink-soft" title={c}>
                    {c.slice(0, CSV_MAX_CELL)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
