import { API } from '@/config'
import type { Artifact } from '@/types/runs'

// Must stay in sync with backend CHART_PLACEHOLDER
// (backend/app/orchestration/aggregator.py).
export const CHART_PLACEHOLDER = 'Chart generated — see Artifacts below.'

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

export function artifactSrc(a: Artifact): string {
  return `${API}${a.url}`
}
