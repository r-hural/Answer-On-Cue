import type { AttemptReport } from '../reports'
import { roundStrings } from '../strings'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.review

/** The model-answer details that go beyond the materials, under the model answer. */
function Beyond({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  const beyond = report.beyond_material ?? []
  if (!report.model_answer || beyond.length === 0) return null
  return (
    <>
      <p>
        <strong>{roundStrings.sources.beyondTitle}</strong>
      </p>
      <ul className="list content" lang={cl.bcp47}>
        {beyond.map((text, index) => (
          <li key={index}>{cl.typeset(text)}</li>
        ))}
      </ul>
    </>
  )
}

function Expanded({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  if (!report.expanded_explanation) return null
  return (
    <details className="disclosure">
      <summary>{t.expanded}</summary>
      <p className="content-body" lang={cl.bcp47}>
        {cl.typeset(report.expanded_explanation)}
      </p>
    </details>
  )
}

/** Weak and no-answer results: the model answer opens by itself, with the key points to remember. */
export function ModelAnswerPanel({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  if (!report.model_answer) return null
  return (
    <section className="panel flow" aria-labelledby="h-model">
      <h2 id="h-model">{t.modelAnswer}</h2>
      <p className="content-body" lang={cl.bcp47}>
        {cl.typeset(report.model_answer)}
      </p>
      <Beyond report={report} />
      {report.key_points.length > 0 && (
        <>
          <h3>{t.keyPoints}</h3>
          <ol className="keypoints" lang={cl.bcp47}>
            {report.key_points.map((point, index) => (
              <li key={index}>{cl.typeset(point)}</li>
            ))}
          </ol>
        </>
      )}
      <Expanded report={report} />
    </section>
  )
}

/** The better version of the learner's answer; the model answer is a closed disclosure for Needs improvement and better. */
export function BetterPanel({ report }: { report: AttemptReport }) {
  const cl = useContentLanguage()
  const better = report.result.assessment?.ai?.better_version
  if (!better) return null
  const display = report.model_answer_display
  const closed = (display === 'one_click' || display === 'on_request') && report.model_answer
  return (
    <section className="panel flow" aria-labelledby="h-better">
      <h2 id="h-better">{t.better}</h2>
      <p className="content-body" lang={cl.bcp47}>
        {cl.typeset(better)}
      </p>
      {closed && (
        <>
          <details className="disclosure">
            <summary>{t.modelAnswer}</summary>
            <p className="content-body" lang={cl.bcp47}>
              {cl.typeset(report.model_answer as string)}
            </p>
            <Beyond report={report} />
            <Expanded report={report} />
          </details>
          <p className="small muted">{display === 'one_click' ? t.modelAnswerOneClick : t.modelAnswerOnRequest}</p>
        </>
      )}
    </section>
  )
}
