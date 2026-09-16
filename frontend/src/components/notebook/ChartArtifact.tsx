import { useState } from 'react'
import type { Artifact } from '@/types/runs'
import { artifactSrc } from '@/components/notebook/artifact'

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
