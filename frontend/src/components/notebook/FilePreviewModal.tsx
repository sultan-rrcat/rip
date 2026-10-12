import DownloadIcon from '@mui/icons-material/Download'
import CloseIcon from '@mui/icons-material/Close'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import type { CSSProperties } from 'react'
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
            {isMarkdown ? (
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
            <p>Preview not available for this file type.</p>
            <p>Use Download to open it locally.</p>
          </div>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
