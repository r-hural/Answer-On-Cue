import type { ReactNode } from 'react'
import type { AnswerBrief, AttemptReport } from '../reports'
import { roundStrings } from '../strings'
import { bandClass, score, seconds } from './format'

const r = roundStrings.repetition
const bands = roundStrings.exam.feedback.bands
/** The header cells' classes, as in the template. */
const HEADER_CLASS = [undefined, undefined, 'r', 'r wrap', 'r wrap', 'r wrap', 'r']

/** This answer as a brief, read from its own report. */
function ownBrief(report: AttemptReport): AnswerBrief {
  const { result } = report
  const scores = result.assessment?.scores ?? null
  return {
    attempt_id: report.attempt_id,
    outcome: result.outcome,
    status: result.status,
    band: result.assessment?.band ?? null,
    factual: scores?.factual ?? null,
    total: scores?.total ?? null,
    preparation_used_ms: result.metrics?.preparation_used_ms ?? null,
    long_pause_count: result.metrics?.long_pause_count ?? null,
    fluency: result.assessment?.ai?.fluency ?? null,
  }
}

const cell = (value: number | null, format: (value: number) => string = score) => (value === null ? '—' : format(value))

function Row({ label, brief }: { label: ReactNode; brief: AnswerBrief }) {
  return (
    <tr>
      <td className="nowrap">{label}</td>
      <td>{brief.band ? <span className={bandClass(brief.band)}>{bands[brief.band]}</span> : (r.noAnswer[brief.outcome] ?? '—')}</td>
      <td className="r">{cell(brief.factual)}</td>
      <td className="r">{cell(brief.total)}</td>
      <td className="r">{cell(brief.preparation_used_ms, seconds)}</td>
      <td className="r">{cell(brief.long_pause_count, String)}</td>
      <td className="r">{cell(brief.fluency)}</td>
    </tr>
  )
}

/** A repeat (Answer again) or retry answer next to the original answer it repeats (feedback--assisted.html). */
export function CompareSection({ report }: { report: AttemptReport }) {
  const original = report.original
  if (!original) return null
  const retry = report.attempt_kind === 'retry'
  return (
    <section className="section flow" aria-labelledby="h-compare">
      <h2 id="h-compare">{r.compareTitle}</h2>
      <div className="table-wrap">
        <table className="table wide middle">
          <thead>
            <tr>
              {r.compareHeaders.map((header, i) => (
                <th key={header} className={HEADER_CLASS[i]}>
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <Row
              label={
                retry ? (
                  r.originalRound
                ) : (
                  <>
                    {r.firstAnswer} <span className="note">{r.counts}</span>
                  </>
                )
              }
              brief={original}
            />
            <Row
              label={
                <>
                  {retry ? r.retryRound : r.secondAnswer} <span className="chip">{r.withHelp}</span>
                </>
              }
              brief={ownBrief(report)}
            />
          </tbody>
        </table>
      </div>
      <p className="small muted">{retry ? r.retryNote : r.compareNote}</p>
    </section>
  )
}
