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
