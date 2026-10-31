import { useState } from 'react'
import type { Job } from './api'
import { libraryStrings } from './strings'

const s = libraryStrings.jobs

const statusChip: Record<Job['status'], string> = {
  queued: 'chip accent',
  running: 'chip accent',
  done: 'chip ok',
  partial: 'chip warn',
  failed: 'chip error',
}

function usageText(job: Job): string | null {
  if (job.status === 'queued' || job.status === 'running') return null
  if (job.usage.length === 0) return job.status === 'done' ? s.usageUnknown : null
  return job.usage
    .map((row) => s.usage(row.calls, row.input_tokens, row.output_tokens, `${row.provider}/${row.model}`))
    .join(' ')
}

/** A task's progress as the template's meter bar; the value is in steps. */
export function JobMeter({ job, label }: { job: Job; label: string }) {
  const total = Math.max(job.steps_total, 1)
  const percent = Math.min(100, Math.round((job.steps_done / total) * 100))
  return (
    <div
      className="meter-bar"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={job.steps_done}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  )
}

function JobRow({ job, onResume }: { job: Job; onResume: (id: number) => Promise<void> }) {
  const [error, setError] = useState<string | null>(null)
  const usage = usageText(job)
  const kind = s.kinds[job.kind] ?? job.kind
  const live = job.status === 'queued' || job.status === 'running'

  async function resume() {
    try {
      setError(null)
      await onResume(job.id)
    } catch (failure) {
      setError((failure as Error).message)
    }
  }

  return (
    <li>
      <div>
        <span className="title">{kind}</span>
        <JobMeter job={job} label={kind} />
        {usage && <p className="meta num">{usage}</p>}
        {job.error_message && (
          <p className="small text-error mt-1" role="alert">
            {job.error_message}
          </p>
        )}
        {error && (
          <p className="small text-error mt-1" role="alert">
            {error}
          </p>
        )}
        {job.resumable && (
          <div className="actions mt-2">
            <button className="btn small" type="button" onClick={resume}>
              {s.resume}
            </button>
          </div>
        )}
      </div>
      <span className={statusChip[job.status] ?? 'chip'}>
        {live && <span className="dot" aria-hidden="true" />}
        {s.statuses[job.status] ?? job.status}
      </span>
    </li>
  )
}

export function JobsPanel({
  jobs,
  onResume,
}: {
  jobs: Job[]
  onResume: (id: number) => Promise<void>
}) {
  return (
    <section id="tasks" aria-labelledby="jobs-title">
      <h2 id="jobs-title">{s.title}</h2>
      {jobs.length === 0 ? (
        <p className="muted small">{s.none}</p>
      ) : (
        <ul className="rows compact">
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} onResume={onResume} />
          ))}
        </ul>
      )}
    </section>
  )
}
