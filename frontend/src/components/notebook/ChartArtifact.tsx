import { useState } from 'react'
import { API } from '@/config'
import type { Artifact } from '@/types/runs'

// Must stay in sync with backend CHART_PLACEHOLDER
// (backend/app/orchestration/aggregator.py).
export const CHART_PLACEHOLDER = 'Chart generated — see Artifacts below.'

// Render-time guard for summaries produced before the backend placeholder
// fix (or any path that still embeds raw SVG): replace SVG markup — bare,
// Step-prefixed, or ```svg fenced — with the placeholder. The chart itself
// renders via the artifact <img> below.
export function stripSvgFromText(text: string | undefined): string | undefined {
  if (!text || !text.toLowerCase().includes('<svg') && !text.includes('```svg')) return text
  return text
    .replace(/```svg[\s\S]*?```/gi, CHART_PLACEHOLDER)
    .replace(/<svg[\s\S]*?(<\/svg>|$)/gi, CHART_PLACEHOLDER)
}

export function artifactSrc(a: Artifact): string {
  return `${API}${a.url}`
}

export default function ArtifactItem({ artifact: a }: { artifact: Artifact }) {
  const [failed, setFailed] = useState(false)
  const isChart = a.kind === 'chart'

  if (!isChart || failed) {
    return (
      <>
        <a
          className="text-blue-600 hover:underline"
          href={artifactSrc(a)}
          download={a.filename}
        >
          {a.filename}
        </a>
        <span className="text-gray-400"> · {a.kind}</span>
      </>
    )
  }

  return (
    <div className="space-y-1">
      <img
        src={artifactSrc(a)}
        alt={a.filename}
        loading="lazy"
        onError={() => setFailed(true)}
        className="max-w-full h-auto rounded border border-gray-200 bg-white"
      />
      <div>
        <a
          className="text-blue-600 hover:underline"
          href={artifactSrc(a)}
          download={a.filename}
        >
          {a.filename}
        </a>
        <span className="text-gray-400"> · {a.kind}</span>
      </div>
    </div>
  )
}
