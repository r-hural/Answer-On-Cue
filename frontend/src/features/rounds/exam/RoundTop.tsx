import { roundStrings } from '../strings'

const t = roundStrings.exam

export type StageIndex = 0 | 1 | 2 | 3 | 4 | 5

export function RoundTop({
  title,
  statuses,
  doneCount,
  stage,
  paused = false,
  stages = t.stages,
  side,
}: {
  title: string
  statuses: ('done' | 'now' | 'pending')[]
  doneCount: number
  stage: StageIndex
  paused?: boolean
  /** The stage names: six with follow-ups on. */
  stages?: readonly string[]
  /** Replaces "n of N done" (the follow-up says whose follow-up it is). */
  side?: string
}) {
  const total = statuses.length
  return (
    <>
      <div className="round-top">
        <h1 className="count">{title}</h1>
        <div className="segments" role="img" aria-label={t.progress(doneCount, total)}>
          {statuses.map((status, i) => (
            <i key={i} className={status === 'pending' ? undefined : status} />
          ))}
        </div>
        <span className="muted">{side ?? t.doneOf(doneCount, total)}</span>
      </div>
      <ol className={paused ? 'stages paused' : 'stages'} aria-label={t.stagesLabel}>
        {stages.map((name, i) => (
          <li key={name} className={i < stage ? 'done' : undefined} aria-current={i === stage ? 'step' : undefined}>
            {name}
          </li>
        ))}
      </ol>
    </>
  )
}
