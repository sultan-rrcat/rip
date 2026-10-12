import { useState } from 'react'
import type { CSSProperties } from 'react'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { Artifact } from '@/types/runs'
import { API } from '@/config'
import { artifactSrc } from '@/components/notebook/artifact'

// Text preview budget: artifacts are small (reports, converted docs),
// but sandbox dumps can be large — never inline more than this.
const ARTIFACT_TEXT_LIMIT = 100 * 1024

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : ''
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

async function fetchArtifactText(a: Artifact): Promise<{ text: string; truncated: boolean }> {
  const res = await fetch(`${API}${a.url}`, { credentials: 'include' })
  if (!res.ok) throw new Error(`Artifact fetch failed: ${res.status}`)
  const text = await res.text()
  if (text.length > ARTIFACT_TEXT_LIMIT) {
    return { text: text.slice(0, ARTIFACT_TEXT_LIMIT), truncated: true }
  }
  return { text, truncated: false }
}

export default function ArtifactItem({ artifact: a }: { artifact: Artifact }) {
  const [failed, setFailed] = useState(false)
  const isChart = a.kind === 'chart'

  if (!isChart || failed) {
    if (!isChart && TEXT_PREVIEWABLE(a)) {
      return <TextArtifact artifact={a} />
    }
    return (
      <DocumentLinks artifact={a} />
    )
  }

  return (
    <div className="space-y-1">
      <img
        src={artifactSrc(a)}
        alt={a.filename}
        loading="lazy"
        onError={() => setFailed(true)}
        className="h-auto max-w-full rounded-md border border-line bg-white"
      />
      <div>
        <a
          className="font-medium text-ledger hover:underline"
          href={artifactSrc(a)}
          download={a.filename}
        >
          {a.filename}
        </a>
        <span className="font-ledger text-ink-soft/60"> · {a.kind}</span>
      </div>
    </div>
  )
}

/** md/txt/json/code-text artifacts render an inline collapsible preview. */
function TEXT_PREVIEWABLE(a: Artifact): boolean {
  if (a.kind !== 'document' && a.kind !== 'data' && a.kind !== 'code') return false
  const ext = extensionOf(a.filename)
  if (a.kind === 'data') return ext === 'json' || ext === 'txt'
  return ['md', 'markdown', 'txt', 'json'].includes(ext) || languageOf(ext) !== 'text' || ext === 'text'
}

function TextArtifact({ artifact: a }: { artifact: Artifact }) {
  const [open, setOpen] = useState(false)
  const [content, setContent] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const ext = extensionOf(a.filename)
  const isMarkdown = ext === 'md' || ext === 'markdown'

  async function toggle() {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    if (content !== null || loading) return
    setLoading(true)
    setError(false)
    try {
      const { text, truncated: cut } = await fetchArtifactText(a)
      setContent(text)
      setTruncated(cut)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          title={open ? 'Hide preview' : 'Show preview'}
          className="font-medium text-ledger hover:underline"
        >
          {a.filename}
        </button>
        <span className="font-ledger text-ink-soft/60"> · {a.kind} · {open ? 'hide' : 'preview'}</span>
      </div>
      {open && (
        <div className="rounded-md border border-line bg-paper p-2">
          {loading && <p className="text-[11px] text-ink-soft">Loading preview…</p>}
          {!loading && error && (
            <p className="text-[11px] text-rust">
              Preview failed — use Download below.
            </p>
          )}
          {!loading && !error && content !== null && (
            <>
              {truncated && (
                <p className="font-ledger mb-1 text-[9px] tracking-wide text-ink-soft/70">
                  TRUNCATED — DOWNLOAD FOR FULL FILE
                </p>
              )}
              {isMarkdown ? (
                <div className="prose prose-sm max-w-none text-[12px]">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                </div>
              ) : (
                <SyntaxHighlighter
                  style={oneLight as { [key: string]: CSSProperties }}
                  language={languageOf(ext)}
                  PreTag="div"
                  customStyle={{ margin: 0, fontSize: '11px', maxHeight: 320, overflow: 'auto' }}
                >
                  {content}
                </SyntaxHighlighter>
              )}
            </>
          )}
          <div className="mt-1">
            <a
              className="text-[11px] font-medium text-ledger hover:underline"
              href={artifactSrc(a)}
              download={a.filename}
            >
              Download {a.filename}
            </a>
          </div>
        </div>
      )}
      {!open && (
        <div>
          <a
            className="text-[11px] font-medium text-ledger hover:underline"
            href={artifactSrc(a)}
            download={a.filename}
          >
            Download
          </a>
        </div>
      )}
    </div>
  )
}

/** Download + open links for non-inline artifacts (pdf/docx/…). */
function DocumentLinks({ artifact: a }: { artifact: Artifact }) {
  const ext = extensionOf(a.filename)
  const openable = ext === 'pdf' || ext === 'html' || ext === 'svg' || ext === 'txt' || ext === 'md'
  return (
    <div>
      <a
        className="font-medium text-ledger hover:underline"
        href={artifactSrc(a)}
        download={a.filename}
      >
        {a.filename}
      </a>
      <span className="font-ledger text-ink-soft/60"> · {a.kind}</span>
      {openable && (
        <>
          <span className="font-ledger text-ink-soft/60"> · </span>
          <a
            className="inline-flex items-center gap-0.5 text-[11px] font-medium text-ledger hover:underline"
            href={artifactSrc(a)}
            target="_blank"
            rel="noreferrer"
            title={`Open ${a.filename} in a new tab`}
          >
            Open <OpenInNewIcon sx={{ fontSize: 12 }} />
          </a>
        </>
      )}
      {ext === 'docx' && (
        <span className="font-ledger block text-[10px] text-ink-soft/60">
          Word file — download to view.
        </span>
      )}
    </div>
  )
}
