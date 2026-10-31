import type { ReactNode } from 'react'
import { Notice } from '../../../ui/Notice'
import type { AttemptReport, PointOut } from '../reports'
import { roundStrings } from '../strings'
import { outsideStrings } from './outsideStrings'
import { SourceQuote } from './SourcesBox'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.review
const src = roundStrings.sources

const ICON = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  viewBox: '0 0 20 20',
  'aria-hidden': true,
}

const HIT = (
  <svg className="hit" {...ICON}>
    <path d="M4.5 10.5l3.5 3.5 7.5-8" />
  </svg>
)
const PART = (
  <svg className="part" {...ICON}>
    <circle cx="10" cy="10" r="7" />
    <path d="M10 3a7 7 0 0 1 0 14z" fill="currentColor" />
  </svg>
)
const MISS = (
  <svg className="miss" {...ICON}>
    <circle cx="10" cy="10" r="7" />
    <path d="M7 10h6" />
  </svg>
)
const WRONG = (
  <svg className="miss" {...ICON}>
    <circle cx="10" cy="10" r="7" />
    <path d="M7.5 7.5l5 5M12.5 7.5l-5 5" />
  </svg>
)

function Item({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  const cl = useContentLanguage()
  return (
    <li>
      {icon}
      <span className="content" lang={cl.bcp47}>
        {children}
      </span>
    </li>
  )
}

/** "beyond your materials" on an expanded point, "partly beyond…" on a mixed one. */
function BasisNote({ point }: { point: PointOut }) {
  if (point.basis !== 'expanded' && point.basis !== 'mixed') return null
  return (
    <span className="note" lang="en">
      {' '}
      {point.basis === 'expanded' ? src.beyond : src.partlyBeyond}
    </span>
  )
}

/** A point the sources disagree on: both passages, and a request to check them. Not graded,
 *  unless the sources disagree on every point (then scoring grades them all). */
function Conflicts({ report, allGraded }: { report: AttemptReport; allGraded: boolean }) {
  const cl = useContentLanguage()
  const conflicts = report.source_conflicts ?? []
  if (conflicts.length === 0) return null
  return (
    <>
      {conflicts.map((conflict, index) => (
        <Notice kind="warn" key={index}>
          <div className="flow">
            <p>
              <strong>{src.conflictTitle}</strong>
              {conflict.point_text && (
                <>
                  {': '}
                  <span className="content" lang={cl.bcp47}>
                    {cl.typeset(conflict.point_text)}
                  </span>
                </>
              )}
            </p>
            <p className="content" lang={cl.bcp47}>
              {cl.typeset(conflict.note)}
            </p>
            {conflict.sources.map((source) => (
              <SourceQuote key={source.passage_id} source={source} />
            ))}
            <p>{allGraded ? src.conflictAllGradedHint : src.conflictHint}</p>
          </div>
        </Notice>
      ))}
    </>
  )
}

function Points({ items, empty }: { items: ReactNode[]; empty: boolean }) {
  if (empty) return <p className="muted">{t.none}</p>
  return <ul className="points">{items}</ul>
}

/** Content, errors, gaps and extras, from the point judgements (feedback headings). */
export function PointsPanel({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  const ai = report.result.assessment?.ai
  if (!ai) return null
  // disputed points are not graded (they appear in the conflict notice only), unless every
  // point is disputed: then scoring grades them all, so they are listed as usual
  const allDisputed = report.points.length > 0 && report.points.every((point) => point.disputed)
  const graded = allDisputed ? report.points : report.points.filter((point) => !point.disputed)
  const byStatus = (...statuses: PointOut['status'][]) => graded.filter((point) => statuses.includes(point.status))
  const correct = byStatus('covered', 'partial')
  const missing = byStatus('missing')
  const wrong = byStatus('wrong')
  // an outside claim the assessment could not judge ("doubtful") is shown once, under extras
  const wrongOutside = ai.outside_material.filter((m) => m.judgement === 'wrong')
  const validOutside = ai.outside_material.filter((m) => m.judgement === 'valid')
  const unsureOutside = ai.outside_material.filter((m) => m.judgement === 'doubtful')
  const errorCount = wrong.length + ai.factual_mistakes.length + wrongOutside.length
  const extraCount = ai.enrichment_mentioned.length + validOutside.length + unsureOutside.length

  return (
    <>
      {ai.transcription_doubts.length > 0 && (
        <Notice kind="warn">
          <div className="flow">
            <p>
              <strong>{t.doubtTitle}</strong>
            </p>
            <ul className="list">
              {ai.transcription_doubts.map((doubt, index) => (
                <li key={index}>
                  <span className="content" lang={cl.bcp47}>
                    {cl.quote(cl.typeset(doubt.quote))}
                  </span>
                  {doubt.likely_meant && (
                    <>
                      {' '}
                      ({t.doubtProbably}{' '}
                      <span className="content" lang={cl.bcp47}>
                        {cl.quote(cl.typeset(doubt.likely_meant))}
                      </span>
                      )
                    </>
                  )}
                  {doubt.reason && (
                    <>
                      {' '}
                      <span className="muted" lang={cl.bcp47}>
                        {cl.typeset(doubt.reason)}
                      </span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </Notice>
      )}
      <Conflicts report={report} allGraded={allDisputed} />
      <section className="panel flow" aria-labelledby="h-content">
        <h2 id="h-content">{t.correct}</h2>
        <Points
          empty={correct.length === 0}
          items={correct.map((point) => (
            <Item key={point.id} icon={point.status === 'partial' ? PART : HIT}>
              {cl.typeset(point.text)}
              <BasisNote point={point} />
              {point.status === 'partial' && (
                <>
                  {' '}
                  <span className="chip warn" lang="en">
                    {t.partly}
                  </span>
                  {point.note && <span className="muted"> {cl.typeset(point.note)}</span>}
                </>
              )}
            </Item>
          ))}
        />

        <h2>{t.errors}</h2>
        <Points
          empty={errorCount === 0}
          items={[
            ...wrong.map((point) => (
              <Item key={`w-${point.id}`} icon={WRONG}>
                {cl.typeset(point.text)}
                <BasisNote point={point} />
                {point.note && <span className="muted"> {cl.typeset(point.note)}</span>}
              </Item>
            )),
            ...ai.factual_mistakes.map((mistake, index) => (
              <Item key={`m-${index}`} icon={WRONG}>
                {cl.quote(cl.typeset(mistake.quote))} {t.mistakeArrow} {cl.typeset(mistake.correction)}
                {mistake.major && (
                  <>
                    {' '}
                    <span className="chip error" lang="en">
                      {t.major}
                    </span>
                  </>
                )}
              </Item>
            )),
            ...wrongOutside.map((material, index) => (
              <Item key={`o-${index}`} icon={WRONG}>
                {cl.quote(cl.typeset(material.quote))}
                {material.note && <span className="muted"> {cl.typeset(material.note)}</span>}
              </Item>
            )),
          ]}
        />

        <h2>{t.missing}</h2>
        <Points
          empty={missing.length === 0}
          items={missing.map((point) => (
            <Item key={point.id} icon={MISS}>
              {cl.typeset(point.text)}
              <BasisNote point={point} />
            </Item>
          ))}
        />

        <h2>{t.extra}</h2>
        <Points
          empty={extraCount === 0}
          items={[
            ...ai.enrichment_mentioned.map((text, index) => (
              <Item key={`e-${index}`} icon={HIT}>
                {cl.typeset(text)}
              </Item>
            )),
            ...validOutside.map((material, index) => (
              <Item key={`x-${index}`} icon={HIT}>
                {cl.quote(cl.typeset(material.quote))}
                {material.note && <span className="muted"> {cl.typeset(material.note)}</span>}
              </Item>
            )),
            ...unsureOutside.map((material, index) => (
              <Item key={`u-${index}`} icon={PART}>
                {cl.quote(cl.typeset(material.quote))}
                {material.note && <span className="muted"> {cl.typeset(material.note)}</span>}
                <span className="note" lang="en">
                  {' '}
                  {t.outsideNote}
                </span>{' '}
                <span className="chip warn" lang="en" title={outsideStrings.checkThisTitle}>
                  {outsideStrings.checkThis}
                </span>
              </Item>
            )),
          ]}
        />
      </section>
    </>
  )
}
