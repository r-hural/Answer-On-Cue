import { useEffect, useRef, useState } from 'react'
import { type Job, getJob, listLiveJobs } from './api'
import { intervals } from './strings'
import { isLive } from './useJobs'

export interface EndedJob {
  job: Job
  at: number
}

const endStatuses = ['done', 'partial', 'failed']

/** A task that really ended; one cut off by a restart (still queued/running, or resumable) is not. */
function hasEnded(job: Job | null): job is Job {
  return job !== null && endStatuses.includes(job.status) && !isLive(job)
}

/** The ended rows younger than intervals.endedTask. */
function fresh(rows: EndedJob[], now: number): EndedJob[] {
  return rows.filter((row) => now - row.at < intervals.endedTask)
}

/**
 * Queued and running tasks of every subject, polled on the jobs interval with one timer while
 * `enabled` and the tab is visible. A task that leaves the live list is fetched once for its end
 * status and kept for intervals.endedTask if it ended; a stale one (cut off by a restart) is dropped.
 * Ended rows are cleared when switched off and pruned when the tab shows again, so old ones do not
 * flash back.
 */
export function useLiveJobs(enabled: boolean): { live: Job[]; ended: EndedJob[] } {
  const [live, setLive] = useState<Job[]>([])
  const [ended, setEnded] = useState<EndedJob[]>([])
  const liveRef = useRef<Job[]>([])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    let busy = false
    const tick = async () => {
      if (busy || document.visibilityState === 'hidden') return
      busy = true
      try {
        const list = (await listLiveJobs()).filter(isLive)
        if (cancelled) return
        const now = Date.now()
        const gone = liveRef.current.filter((job) => !list.some((item) => item.id === job.id))
        liveRef.current = list
        setLive(list)
        const finished = await Promise.all(gone.map((job) => getJob(job.id).catch(() => null)))
        if (cancelled) return
        const added = finished.filter(hasEnded).map((job) => ({ job, at: now }))
        setEnded((current) => {
          const kept = fresh(current, now)
          return added.length === 0 && kept.length === current.length ? current : [...kept, ...added]
        })
      } catch {
        // the next tick tries again
      } finally {
        busy = false
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), intervals.jobs)
    const onVisible = () => {
      setEnded((current) => {
        const kept = fresh(current, Date.now())
        return kept.length === current.length ? current : kept
      })
      void tick()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
      setEnded([]) // switched off: nothing old shows on return
    }
  }, [enabled])

  return { live, ended }
}
