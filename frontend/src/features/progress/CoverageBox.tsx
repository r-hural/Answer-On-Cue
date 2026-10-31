import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getSubjectCoverage, type SubjectCoverage } from './coverage'
import { STATUSES, statusClass } from './coverageFormat'
import { progressStrings } from './strings'

const t = progressStrings.coverage

/** The subject page's Coverage box: topics per status. It stays out of the way until there is something to show. */
export function CoverageBox({ subjectId, chapterId }: { subjectId: number; chapterId?: number | null }) {
  const [loaded, setLoaded] = useState<SubjectCoverage | null>(null)

  useEffect(() => {
    let cancelled = false
    // chapter_id only when given, so a one-chapter subject asks exactly as before
    getSubjectCoverage(subjectId, 'all', chapterId)
      .then((coverage) => !cancelled && setLoaded(coverage))
      .catch(() => undefined) // the box is optional: no box rather than an error on the subject page
    return () => {
      cancelled = true
    }
  }, [subjectId, chapterId])

  if (!loaded || loaded.subject_id !== subjectId || loaded.topics.length === 0) return null
  return (
    <section className="box tint" aria-labelledby="cov-h">
      <h2 id="cov-h">{t.boxTitle}</h2>
      <dl className="facts num">
        {STATUSES.map((key) => (
          <Fragment key={key}>
            <dt>
              <span className={`status ${statusClass(key)}`}>{t.status[key]}</span>
            </dt>
            <dd>{t.topics(loaded.topic_counts[key])}</dd>
          </Fragment>
        ))}
      </dl>
      <p className="mt-4">
        <Link to={`/progress/coverage?subject=${subjectId}${chapterId == null ? '' : `&chapter=${chapterId}`}`}>
          {t.boxLink}
        </Link>
      </p>
    </section>
  )
}
