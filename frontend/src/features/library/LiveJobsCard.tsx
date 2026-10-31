import { Link, useLocation } from 'react-router-dom'
import { plNbsp } from '../../i18n/polish'
import type { Job } from './api'
import { GuidedDialogs } from './GuidedDialogs'
import { JobMeter } from './JobsPanel'
import { libraryStrings } from './strings'
import { useLiveJobs } from './useLiveJobs'

const s = libraryStrings.jobs

/** The round and recording screens keep their controls clear: no card there. */
export function hidesTaskCard(pathname: string): boolean {
  return pathname.startsWith('/rounds/') || pathname.endsWith('/microphone') || pathname === '/microphone'
}

function TaskName({ job }: { job: Job }) {
  return (
    <span className="task-name content">
      {plNbsp(job.document_title ?? job.subject_name)}
    </span>
  )
}

/** Live AI tasks of every subject, bottom right on every shell page; a finished one stays 10 s. */
export function LiveJobsCard() {
  const { pathname } = useLocation()
  const hidden = hidesTaskCard(pathname)
  const { live, ended } = useLiveJobs(!hidden)
  const empty = hidden || (live.length === 0 && ended.length === 0)
  // one shape whether the card shows or not, so the dialogs (rendered into document.body) keep their state
  return (
    <>
      {!empty && (
        <aside className="task-float" aria-label={s.float}>
          <ul>
            {live.map((job) => {
              const kind = s.kinds[job.kind] ?? job.kind
              return (
                <li key={job.id}>
                  <Link to={`/library/${job.subject_id}`}>
                    <span className="task-line">
                      <strong>{kind}</strong>
                      <TaskName job={job} />
                      <span className="num muted">{s.steps(job.steps_done, job.steps_total)}</span>
                    </span>
                    <JobMeter job={job} label={kind} />
                  </Link>
                </li>
              )
            })}
            {ended.map(({ job }) => (
              <li key={job.id}>
                <Link to={`/library/${job.subject_id}`}>
                  <span className="task-line">
                    <strong>{s.kinds[job.kind] ?? job.kind}</strong>
                    <TaskName job={job} />
                    <span className={job.status === 'failed' ? 'chip error' : job.status === 'partial' ? 'chip warn' : 'chip ok'}>
                      {s.ended[job.status] ?? s.statuses[job.status] ?? job.status}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </aside>
      )}
      <GuidedDialogs ended={ended} hidden={hidden} />
    </>
  )
}
