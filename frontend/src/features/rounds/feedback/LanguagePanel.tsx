import { Fragment } from 'react'
import type { AttemptReport } from '../reports'
import { roundStrings } from '../strings'
import { SpeedPanel } from './SpeedPanel'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.review
const STYLE_KEYS = ['formality', 'terminology', 'structure', 'precision'] as const

/** Language and wording, exam answer style, speed and fluency, and what to improve. */
export function LanguagePanel({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  const ai = report.result.assessment?.ai
  if (!ai) return null
  const register = ai.language_register

  return (
    <section className="panel flow" aria-labelledby="h-language">
      <h2 id="h-language">{t.language}</h2>
      {(['grammar', 'vocabulary'] as const).map((key) => (
        <p key={key} className="content reading" lang={cl.bcp47}>
          {cl.typeset(register[key].summary)}
        </p>
      ))}
      {ai.language_issues.length > 0 && (
        <ul className="list">
          {ai.language_issues.map((issue, index) => (
            <li key={index} className="content" lang={cl.bcp47}>
              {cl.quote(cl.typeset(issue.quote))} → {cl.quote(cl.typeset(issue.suggestion))}
              {issue.explanation && <span className="muted"> {cl.typeset(issue.explanation)}</span>}
            </li>
          ))}
        </ul>
      )}

      <h2>{t.style}</h2>
      <dl className="facts">
        {STYLE_KEYS.map((key) => (
          <Fragment key={key}>
            <dt>{t.registerLabels[key]}</dt>
            <dd className="content" lang={cl.bcp47}>
              {cl.typeset(register[key].summary)}
            </dd>
          </Fragment>
        ))}
      </dl>

      <SpeedPanel report={report} />

      {ai.improvements.length > 0 && (
        <>
          <h3>{t.improve}</h3>
          <ul className="list">
            {ai.improvements.map((text, index) => (
              <li key={index} className="content" lang={cl.bcp47}>
                {cl.typeset(text)}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
