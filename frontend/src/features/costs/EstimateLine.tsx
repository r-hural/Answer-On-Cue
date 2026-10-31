import { useEffect, useState } from 'react'
import {
  type GenerationBody,
  type JobEstimate,
  fetchExtractionEstimate,
  fetchGenerationEstimate,
} from './api'
import { aboutMinutes, usd } from './format'
import { costsStrings as s } from './strings'

/** The cost of a library job before it starts: question generation (the body the Generate
 * button would send) or topic extraction (a document version). A failed estimate shows nothing;
 * a job that does not fit is still offered: the server decides, the line explains. */
export function EstimateLine({
  generation = null,
  extraction = null,
  inline = false,
}: {
  generation?: GenerationBody | null
  extraction?: number | null
  /** Inside a row's meta line: a span instead of a paragraph. */
  inline?: boolean
}) {
  const [estimate, setEstimate] = useState<{ key: string; value: JobEstimate } | null>(null)
  const key = generation ? `g:${JSON.stringify(generation)}` : extraction !== null ? `e:${extraction}` : ''

  useEffect(() => {
    if (!key) return
    let cancelled = false
    const request = key.startsWith('g:')
      ? fetchGenerationEstimate(JSON.parse(key.slice(2)) as GenerationBody)
      : fetchExtractionEstimate(Number(key.slice(2)))
    request
      .then((value) => !cancelled && setEstimate({ key, value }))
      .catch(() => !cancelled && setEstimate(null))
    return () => {
      cancelled = true
    }
  }, [key])

  if (!estimate || estimate.key !== key) return null
  const value = estimate.value
  const text =
    value.kind === 'topics.extract'
      ? s.extractionLine(usd(value.usd))
      : s.generationLine(value.questions, aboutMinutes(value.minutes), usd(value.usd))
  const left = value.month.remaining_usd
  const over = !value.fits && left !== null && (
    <span className="text-error">{s.onlyLeft(usd(left), usd(value.month.ceiling_usd))}</span>
  )
  if (inline)
    return (
      <span className="hint">
        {text}
        {over}
      </span>
    )
  return (
    <p className="hint num">
      {text}
      {over}
    </p>
  )
}
