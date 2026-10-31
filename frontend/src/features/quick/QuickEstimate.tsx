import { useEffect, useState } from 'react'
import { type GenerationBody, fetchGenerationEstimate } from '../costs/api'
import { usd } from '../costs/format'
import { quickStrings } from './strings'

const c = quickStrings.costs

/** "Estimated AI usage: about $0.06. Uses your OpenAI key." (quick--topics.html). The key line
 * stays its own element; the estimate goes in front of it once known. */
export function QuickEstimate({ body, uses }: { body: GenerationBody | null; uses: string }) {
  const key = body ? JSON.stringify(body) : ''
  const [estimate, setEstimate] = useState<{ key: string; usd: number } | null>(null)

  useEffect(() => {
    if (!key) return
    let cancelled = false
    fetchGenerationEstimate(JSON.parse(key) as GenerationBody)
      .then((value) => !cancelled && setEstimate({ key, usd: value.usd }))
      .catch(() => !cancelled && setEstimate(null))
    return () => {
      cancelled = true
    }
  }, [key])

  const shown = estimate && estimate.key === key ? estimate : null
  return (
    <p className="small mt-4">
      {shown && (
        <>
          {c.estimated} <strong className="num">{c.about(usd(shown.usd))}</strong>.{' '}
        </>
      )}
      <span>{uses}</span>
    </p>
  )
}
