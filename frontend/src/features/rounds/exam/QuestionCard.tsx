import type { ReactNode } from 'react'
import { questionSize } from '../clock'
import { roundStrings } from '../strings'
import { useContentLanguage } from '../../language/ContentLanguage'

const t = roundStrings.exam

export function QuestionCard({
  text,
  difficulty,
  replay,
  timer,
  followUp = false,
}: {
  text: string
  difficulty: string
  replay?: ReactNode
  timer: ReactNode
  /** The follow-up to the main question, built from the learner's answer. */
  followUp?: boolean
}) {
  const cl = useContentLanguage()
  return (
    <section className="panel exam-card" aria-label={followUp ? t.followUpRegion : t.questionRegion}>
      <div>
        <p className="content-q" data-size={questionSize(text)} lang={cl.bcp47}>
          {cl.typeset(text)}
        </p>
        <div className="q-after">
          {replay}
          <p className="q-meta">
            {followUp ? t.followUpQuestion : t.mainQuestion} <span aria-hidden="true">·</span>{' '}
            {followUp ? t.followUpBasedOn : (t.difficulties[difficulty] ?? difficulty)}
          </p>
        </div>
      </div>
      {timer}
    </section>
  )
}
