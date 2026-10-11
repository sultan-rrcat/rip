import { API } from '@/config'
import type { Artifact } from '@/types/runs'

// Must stay in sync with backend CHART_PLACEHOLDER / QUIZ_PLACEHOLDER
// (backend/app/orchestration/aggregator.py).
export const CHART_PLACEHOLDER = 'Chart generated — see Artifacts below.'
export const QUIZ_PLACEHOLDER = 'Quiz ready — take the test below.'

// Render-time guard for summaries produced before the backend placeholder
// fix (or any path that still embeds raw SVG): replace SVG markup — bare,
// Step-prefixed, or ```svg fenced — with the placeholder. The chart itself
// renders via the artifact <img>.
export function stripSvgFromText(text: string | undefined): string | undefined {
  if (
    !text ||
    (!text.toLowerCase().includes('<svg') && !text.includes('```svg'))
  ) {
    return text
  }
  return text
    .replace(/```svg[\s\S]*?```/gi, CHART_PLACEHOLDER)
    .replace(/<svg[\s\S]*?(<\/svg>|$)/gi, CHART_PLACEHOLDER)
}

// Guard for quiz paths that leak raw {"questions": [...]} JSON into chat:
// the interactive test renders via the QuizArtifact component instead.
export function stripQuizJsonFromText(text: string | undefined): string | undefined {
  if (!text || !text.includes('"questions"')) return text
  const trimmed = text.trim()
  if (trimmed.startsWith('{') && trimmed.includes('"questions"')) {
    try {
      const obj = JSON.parse(trimmed.slice(trimmed.indexOf('{'))) as {
        questions?: unknown
      }
      if (obj && Array.isArray(obj.questions)) return QUIZ_PLACEHOLDER
    } catch {
      // fall through to fenced stripping
    }
  }
  return text
    .replace(/```json[\s\S]*?"questions"[\s\S]*?```/gi, QUIZ_PLACEHOLDER)
    .replace(/```[\s\S]*?"questions"[\s\S]*?```/gi, QUIZ_PLACEHOLDER)
}

export function stripArtifactsFromText(text: string | undefined): string | undefined {
  return stripQuizJsonFromText(stripSvgFromText(text))
}

export function artifactSrc(a: Artifact): string {
  return `${API}${a.url}`
}
