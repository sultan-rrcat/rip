import { useEffect, useState } from 'react'
import type { Artifact } from '@/types/runs'
import { artifactSrc } from '@/components/notebook/artifact'

export const QUIZ_PLACEHOLDER = 'Quiz ready — take the test below.'

export interface QuizOption {
  id: string
  text: string
}

export interface QuizQuestion {
  id: string
  type: 'mcq' | 'msq' | 'truefalse'
  stem: string
  options: QuizOption[]
  answers: string[]
  explanation?: string
}

export interface QuizPayload {
  title: string
  questions: QuizQuestion[]
}

export function stripQuizJsonFromText(text: string | undefined): string | undefined {
  if (!text || !text.includes('"questions"')) return text
  const trimmed = text.trim()
  // Whole-bubble raw JSON leak → placeholder (interactive test renders via artifact).
  if (trimmed.startsWith('{') && trimmed.includes('"questions"')) {
    const first = trimmed.indexOf('{')
    try {
      const obj = JSON.parse(trimmed.slice(first))
      if (obj && Array.isArray((obj as { questions?: unknown }).questions)) {
        return QUIZ_PLACEHOLDER
      }
    } catch {
      // Fall through to fenced/inline stripping below.
    }
  }
  return text
    .replace(/```json[\s\S]*?"questions"[\s\S]*?```/gi, QUIZ_PLACEHOLDER)
    .replace(/```[\s\S]*?"questions"[\s\S]*?```/gi, QUIZ_PLACEHOLDER)
}

function isCorrect(q: QuizQuestion, selected: string[]): boolean {
  const a = [...q.answers].sort().join('|')
  const b = [...selected].sort().join('|')
  return a === b
}

export default function QuizArtifact({ artifact: a }: { artifact: Artifact }) {
  const [quiz, setQuiz] = useState<QuizPayload | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [answers, setAnswers] = useState<Record<string, string[]>>({})
  const [submitted, setSubmitted] = useState(false)
  const [showAnswers, setShowAnswers] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(artifactSrc(a), { credentials: 'include' })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = (await res.json()) as QuizPayload
        if (!data || !Array.isArray(data.questions)) throw new Error('bad quiz JSON')
        if (!cancelled) setQuiz(data)
      } catch (e) {
        if (!cancelled) setFailed(e instanceof Error ? e.message : 'load failed')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [a])

  if (failed || !quiz) {
    return (
      <>
        {failed ? (
          <span className="text-rust">Quiz failed to load ({failed}). </span>
        ) : (
          <span className="text-ink-soft">Loading quiz… </span>
        )}
        <a
          className="font-medium text-ledger hover:underline"
          href={artifactSrc(a)}
          download={a.filename}
        >
          {a.filename}
        </a>
        <span className="font-ledger text-ink-soft/60"> · {a.kind}</span>
      </>
    )
  }

  const total = quiz.questions.length
  const answered = quiz.questions.filter((q) => (answers[q.id] ?? []).length > 0).length
  const score = submitted
    ? quiz.questions.filter((q) => isCorrect(q, answers[q.id] ?? [])).length
    : 0

  function toggle(q: QuizQuestion, optId: string) {
    if (submitted) return
    setAnswers((prev) => {
      const cur = prev[q.id] ?? []
      if (q.type === 'msq') {
        return {
          ...prev,
          [q.id]: cur.includes(optId) ? cur.filter((x) => x !== optId) : [...cur, optId],
        }
      }
      return { ...prev, [q.id]: [optId] }
    })
  }

  function reset() {
    setAnswers({})
    setSubmitted(false)
    setShowAnswers(false)
  }

  return (
    <div className="space-y-3 rounded-lg border border-line bg-paper p-3">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-ink">{quiz.title || 'Quiz'}</p>
        <p className="font-ledger text-[11px] text-ink-soft/70">
          {total} questions{submitted ? ` · score ${score}/${total}` : ` · ${answered}/${total} answered`}
        </p>
      </div>

      {submitted && (
        <div
          role="status"
          className="rounded-md border border-ledger/40 bg-card px-3 py-2 text-[13px] font-semibold text-ink"
        >
          Score: {score} / {total} ({total ? Math.round((score / total) * 100) : 0}%)
        </div>
      )}

      <ol className="space-y-3">
        {quiz.questions.map((q, qi) => {
          const sel = answers[q.id] ?? []
          const graded = submitted || showAnswers
          return (
            <li key={q.id} className="rounded-md border border-line bg-card p-3">
              <p className="text-[13px] font-medium leading-relaxed text-ink">
                <span className="mr-1.5 font-ledger text-[11px] text-ink-soft/70">
                  Q{qi + 1} · {q.type.toUpperCase()}
                </span>
                {q.stem}
              </p>
              <div className="mt-2 space-y-1.5">
                {q.options.map((o) => {
                  const checked = sel.includes(o.id)
                  const isAnswer = q.answers.includes(o.id)
                  const mark = graded
                    ? isAnswer
                      ? 'border-ledger bg-ledger/10'
                      : checked
                        ? 'border-rust bg-rust/10'
                        : 'border-line'
                    : checked
                      ? 'border-ink bg-ink/[0.04]'
                      : 'border-line'
                  return (
                    <label
                      key={o.id}
                      className={`flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-1.5 text-[13px] ${mark}`}
                    >
                      <input
                        type={q.type === 'msq' ? 'checkbox' : 'radio'}
                        name={`quiz-${a.artifact_id}-${q.id}`}
                        checked={checked}
                        disabled={submitted}
                        onChange={() => toggle(q, o.id)}
                        className="mt-1"
                      />
                      <span className="leading-relaxed text-ink/90">{o.text}</span>
                      {graded && isAnswer && (
                        <span className="ml-auto font-ledger text-[10px] font-semibold text-ledger">
                          ✓
                        </span>
                      )}
                    </label>
                  )
                })}
              </div>
              {graded && submitted && (
                <p className="mt-1.5 text-[12px] font-medium text-ink-soft">
                  {isCorrect(q, sel) ? 'Correct.' : 'Incorrect.'}
                  {q.explanation ? ` ${q.explanation}` : ''}
                </p>
              )}
              {showAnswers && !submitted && (
                <p className="mt-1.5 text-[12px] text-ink-soft">
                  Answer: {q.answers.join(', ')}
                  {q.explanation ? ` — ${q.explanation}` : ''}
                </p>
              )}
            </li>
          )
        })}
      </ol>

      <div className="flex flex-wrap gap-2">
        {!submitted ? (
          <button
            type="button"
            onClick={() => setSubmitted(true)}
            disabled={answered === 0}
            className="h-8 rounded-md bg-ink px-4 text-[13px] font-semibold text-paper transition-colors hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-40"
          >
            Submit ({answered}/{total})
          </button>
        ) : (
          <button
            type="button"
            onClick={reset}
            className="h-8 rounded-md bg-ink px-4 text-[13px] font-semibold text-paper transition-colors hover:bg-ink-soft"
          >
            Retry
          </button>
        )}
        <button
          type="button"
          onClick={() => setShowAnswers((v) => !v)}
          className="h-8 rounded-md border border-line bg-card px-3 text-[13px] font-medium text-ink-soft transition-colors hover:bg-paper"
        >
          {showAnswers ? 'Hide answers' : 'Show answers'}
        </button>
        <a
          className="inline-flex h-8 items-center rounded-md border border-line bg-card px-3 text-[13px] font-medium text-ledger hover:underline"
          href={artifactSrc(a)}
          download={a.filename}
        >
          {a.filename}
        </a>
      </div>
    </div>
  )
}
