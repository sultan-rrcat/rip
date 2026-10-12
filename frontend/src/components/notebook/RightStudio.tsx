import QuizIcon from '@mui/icons-material/Quiz'
import SwapHorizIcon from '@mui/icons-material/SwapHoriz'
import CloseIcon from '@mui/icons-material/Close'
import RefreshIcon from '@mui/icons-material/Refresh'
import StopIcon from '@mui/icons-material/Stop'
import { memo, useState } from 'react'
import ArtifactItem from '@/components/notebook/ChartArtifact'
import QuizArtifact from '@/components/notebook/QuizArtifact'
import type { NotebookFile } from '@/types'
import type {
  StudioConvertFormat,
  StudioJob,
  StudioQuizKind,
} from '@/types/studio'
import { isTerminalStatus } from '@/types/studio'

interface RightStudioProps {
  files: NotebookFile[]
  jobs: StudioJob[]
  loading: boolean
  error: string | null
  starting: null | 'quiz' | 'convert'
  onStartQuiz: (count: number, kind: StudioQuizKind) => Promise<StudioJob | null>
  onStartConvert: (
    fileId: string | '*',
    targetFormat: StudioConvertFormat,
  ) => Promise<StudioJob | null>
  onCancel: (jobId: string) => void
  onReload: () => void
}

const QUIZ_KINDS: { value: StudioQuizKind; label: string }[] = [
  { value: 'mcq', label: 'MCQ' },
  { value: 'msq', label: 'MSQ' },
  { value: 'truefalse', label: 'T/F' },
]

const CONVERT_FORMATS: { value: StudioConvertFormat; label: string }[] = [
  { value: 'md', label: 'MD' },
  { value: 'docx', label: 'DOCX' },
  { value: 'pdf', label: 'PDF' },
]

// Mirrors backend doc.convert _SOURCE_EXTS (.pdf/.docx only).
function isConvertible(name: string): boolean {
  const dot = name.lastIndexOf('.')
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : ''
  return ext === '.pdf' || ext === '.docx'
}

function statusTone(status: string, resultStatus: string): string {
  if (status === 'failed' || status === 'cancelled') return 'bg-rust'
  if (status !== 'completed') return 'bg-brass-deep'
  if (resultStatus === 'partial') return 'bg-brass-deep'
  if (resultStatus === 'failed') return 'bg-rust'
  return 'bg-emerald-500'
}

// Row verdict: the run-row status, refined by the aggregator verdict so a
// PARTIAL run (tool step failed honest, no artifacts) never reads COMPLETED.
function verdictLabel(job: StudioJob): string {
  if (job.status !== 'completed') return job.status.toUpperCase()
  if (job.result_status === 'partial') return 'PARTIAL'
  if (job.result_status === 'failed') return 'FAILED'
  return 'COMPLETED'
}

function paramsSummary(job: StudioJob): string {
  if (job.kind === 'quiz') {
    const count = job.params.count ?? '?'
    const kind = job.params.kind ?? ''
    return `${count} × ${String(kind).toUpperCase()}`
  }
  const fmt = String(job.params.target_format ?? '').toUpperCase()
  const name = job.params.file_name
    ? String(job.params.file_name)
    : job.params.file_id === '*'
      ? 'All documents'
      : 'Document'
  return `${name} → ${fmt}`
}

const RightStudio = memo(function RightStudio({
  files,
  jobs,
  loading,
  error,
  starting,
  onStartQuiz,
  onStartConvert,
  onCancel,
  onReload,
}: RightStudioProps) {
  const [quizCount, setQuizCount] = useState(10)
  const [quizKind, setQuizKind] = useState<StudioQuizKind>('mcq')
  const [convertFile, setConvertFile] = useState<string>('*')
  const [convertFormat, setConvertFormat] = useState<StudioConvertFormat>('pdf')

  const convertible = files.filter(
    (f) => f.status === 'ready' && !f.id.startsWith('temp-') && isConvertible(f.name),
  )
  const busy = starting !== null

  function clampCount(n: number): number {
    if (Number.isNaN(n)) return 10
    return Math.min(50, Math.max(2, Math.floor(n)))
  }

  return (
    <div className="flex h-full w-[320px] shrink-0 flex-col gap-2">
      <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-card shadow-[0_1px_0_rgba(21,39,54,0.12)]">
        <div className="m-3 mb-0 flex items-center justify-between rounded-md border border-line/70 bg-paper p-3">
          <div>
            <h1 className="font-display text-[15px] font-bold leading-tight text-ink">
              Studio
            </h1>
            <p className="font-ledger mt-1 text-[10px] tracking-[0.14em] text-ink-soft/70">
              {jobs.length} JOB{jobs.length !== 1 ? 'S' : ''} · ONE-CLICK TOOLS
            </p>
          </div>
          <button
            type="button"
            onClick={onReload}
            title="Refresh job history"
            aria-label="Refresh job history"
            className="flex h-9 w-9 items-center justify-center rounded-full bg-ink text-paper transition-colors hover:bg-ink-soft"
          >
            <RefreshIcon fontSize="small" />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
          {error && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-rust/50 bg-rust/[0.07] px-2.5 py-2 text-[11px] leading-snug text-rust"
            >
              <span className="min-w-0 flex-1">{error}</span>
            </div>
          )}

          {/* Quiz tool */}
          <section
            aria-label="Generate quiz"
            className="rounded-md border border-line/70 bg-paper/50 p-3"
          >
            <div className="flex items-center gap-2">
              <QuizIcon fontSize="small" className="text-ledger" aria-hidden="true" />
              <h2 className="text-[13px] font-semibold text-ink">Quiz</h2>
            </div>
            <p className="mt-1 text-[11px] leading-snug text-ink-soft/80">
              Interactive test from your documents — same engine as chat quizzes.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <label
                htmlFor="studio-quiz-count"
                className="font-ledger text-[10px] font-semibold tracking-wide text-ink-soft/70"
              >
                QUESTIONS
              </label>
              <input
                id="studio-quiz-count"
                type="number"
                min={2}
                max={50}
                value={quizCount}
                disabled={busy}
                onChange={(e) => setQuizCount(clampCount(Number(e.target.value)))}
                className="h-8 w-16 rounded-md border border-line bg-card px-2 text-[13px] text-ink outline-none focus:border-ledger/60 disabled:opacity-40"
              />
              <div className="flex gap-1" role="radiogroup" aria-label="Question type">
                {QUIZ_KINDS.map((k) => (
                  <button
                    key={k.value}
                    type="button"
                    role="radio"
                    aria-checked={quizKind === k.value}
                    disabled={busy}
                    onClick={() => setQuizKind(k.value)}
                    className={`font-ledger rounded border px-2 py-1 text-[10px] font-semibold tracking-wide transition-colors disabled:opacity-40 ${
                      quizKind === k.value
                        ? 'border-ink bg-ink text-paper'
                        : 'border-line bg-card text-ink-soft hover:text-ink'
                    }`}
                  >
                    {k.label}
                  </button>
                ))}
              </div>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void onStartQuiz(clampCount(quizCount), quizKind)}
              className="mt-2 flex w-full items-center justify-center gap-2 rounded-md bg-ink py-2 text-sm font-semibold text-paper transition-colors hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-40"
            >
              {starting === 'quiz' ? 'Starting…' : 'Generate quiz'}
            </button>
          </section>

          {/* Convert tool */}
          <section
            aria-label="Convert document"
            className="rounded-md border border-line/70 bg-paper/50 p-3"
          >
            <div className="flex items-center gap-2">
              <SwapHorizIcon fontSize="small" className="text-ledger" aria-hidden="true" />
              <h2 className="text-[13px] font-semibold text-ink">Convert</h2>
            </div>
            <p className="mt-1 text-[11px] leading-snug text-ink-soft/80">
              Lossless PDF/DOCX re-render — no LLM, no paraphrase.
            </p>
            <label
              htmlFor="studio-convert-file"
              className="font-ledger mt-2 block text-[10px] font-semibold tracking-wide text-ink-soft/70"
            >
              SOURCE FILE
            </label>
            <select
              id="studio-convert-file"
              value={convertFile}
              disabled={busy}
              onChange={(e) => setConvertFile(e.target.value)}
              className="mt-1 h-8 w-full rounded-md border border-line bg-card px-2 text-[12px] text-ink outline-none focus:border-ledger/60 disabled:opacity-40"
            >
              <option value="*">All documents</option>
              {convertible.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
            {convertible.length === 0 && (
              <p className="font-ledger mt-1 text-[10px] tracking-wide text-ink-soft/50">
                NO READY PDF/DOCX — CONVERT-ALL STILL AVAILABLE
              </p>
            )}
            <div className="mt-2 flex gap-1" role="radiogroup" aria-label="Target format">
              {CONVERT_FORMATS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  role="radio"
                  aria-checked={convertFormat === f.value}
                  disabled={busy}
                  onClick={() => setConvertFormat(f.value)}
                  className={`font-ledger rounded border px-2 py-1 text-[10px] font-semibold tracking-wide transition-colors disabled:opacity-40 ${
                    convertFormat === f.value
                      ? 'border-ink bg-ink text-paper'
                      : 'border-line bg-card text-ink-soft hover:text-ink'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void onStartConvert(convertFile, convertFormat)}
              className="mt-2 flex w-full items-center justify-center gap-2 rounded-md bg-ink py-2 text-sm font-semibold text-paper transition-colors hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-40"
            >
              {starting === 'convert' ? 'Starting…' : 'Convert'}
            </button>
          </section>

          {/* History */}
          <section aria-label="Studio history" className="space-y-2">
            <h2 className="font-ledger px-1 text-[10px] font-semibold tracking-[0.18em] text-ink-soft/60">
              HISTORY
            </h2>
            {loading && jobs.length === 0 && (
              <p className="px-1 text-[12px] text-ink-soft/60">Loading jobs…</p>
            )}
            {!loading && jobs.length === 0 && (
              <p className="px-1 text-[12px] text-ink-soft/60">
                No Studio jobs yet — generate a quiz or convert a file above.
              </p>
            )}
            {jobs.map((job) => {
              const running = !isTerminalStatus(job.status)
              return (
                <article
                  key={job.job_id}
                  className="rounded-md border border-line/70 bg-paper/50 p-2.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span
                        title={`Status: ${verdictLabel(job)}`}
                        aria-label={`Status: ${verdictLabel(job)}`}
                        className={`inline-block h-2 w-2 shrink-0 rounded-full ${statusTone(job.status, job.result_status)}`}
                      />
                      <p className="font-ledger truncate text-[10px] font-semibold tracking-wide text-ink-soft/70">
                        {job.kind.toUpperCase()} · {paramsSummary(job)}
                      </p>
                    </div>
                    {running ? (
                      <button
                        type="button"
                        onClick={() => onCancel(job.job_id)}
                        title="Stop job"
                        aria-label={`Stop ${job.kind} job`}
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-rust text-white transition-colors hover:bg-rust/85"
                      >
                        <StopIcon sx={{ fontSize: 14 }} />
                      </button>
                    ) : (
                      <span className="font-ledger shrink-0 text-[9px] tracking-wide text-ink-soft/50">
                        {verdictLabel(job)}
                      </span>
                    )}
                  </div>
                  {running && (
                    <p className="mt-1 flex items-center gap-1.5 text-[11px] text-brass-deep">
                      <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-line border-t-brass-deep" />
                      Working — history refreshes automatically.
                    </p>
                  )}
                  {!running && job.artifacts.length === 0 && (
                    <div className="mt-1.5 border-t border-line/60 pt-1.5">
                      <p className="font-ledger text-[10px] font-semibold tracking-wide text-brass-deep">
                        NO FILES PRODUCED — {verdictLabel(job)}
                      </p>
                      {job.summary && (
                        <p className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap break-words text-[11px] leading-snug text-ink-soft">
                          {job.summary}
                        </p>
                      )}
                    </div>
                  )}
                  {job.artifacts.length > 0 && (
                    <ul className="mt-1.5 space-y-1.5 border-t border-line/60 pt-1.5">
                      {job.artifacts.map((a) => (
                        <li key={a.artifact_id} className="text-[11px]">
                          {a.kind === 'quiz' ? (
                            <QuizArtifact artifact={a} />
                          ) : (
                            <ArtifactItem artifact={a} />
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </article>
              )
            })}
          </section>
        </div>
      </div>
    </div>
  )
})

export default RightStudio

export function StudioCloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      aria-label="Close studio panel"
      className="flex h-8 w-8 items-center justify-center rounded-md text-ink-soft/70 transition-colors hover:bg-paper hover:text-ink"
    >
      <CloseIcon sx={{ fontSize: 18 }} />
    </button>
  )
}
