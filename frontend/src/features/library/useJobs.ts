import { useCallback, useEffect, useRef, useState } from 'react'
import { type Job, getJob, listJobs, resumeJob } from './api'
import { intervals } from './strings'

/** A task this page should keep polling: running here, not cut off by a restart. */
export function isLive(job: Job): boolean {
  return (job.status === 'queued' || job.status === 'running') && !job.resumable
}

/**
 * The latest tasks of a subject. Live jobs found on load are picked up again, and every live task
 * is polled until it ends; `onFinished` runs once for each task that stops being live.
 */
export function useJobs(subjectId: number, onFinished: (job: Job) => void) {
  const [jobs, setJobs] = useState<Job[]>([])
  const jobsRef = useRef<Job[]>([])
  const finished = useRef(onFinished)
  useEffect(() => {
    finished.current = onFinished
  })

  const merge = useCallback((job: Job) => {
    const others = jobsRef.current.filter((item) => item.id !== job.id)
    const next = [job, ...others].sort((a, b) => b.id - a.id).slice(0, 5)
    jobsRef.current = next
    setJobs(next)
  }, [])

  useEffect(() => {
    let cancelled = false
    jobsRef.current = []
    listJobs(subjectId)
      .then((list) => {
        if (cancelled) return
        jobsRef.current = list
        setJobs(list)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [subjectId])

  const anyLive = jobs.some(isLive)
  useEffect(() => {
    if (!anyLive) return
    const timer = window.setInterval(async () => {
      for (const job of jobsRef.current.filter(isLive)) {
        try {
          const next = await getJob(job.id)
          merge(next)
          if (!isLive(next)) finished.current(next)
        } catch {
          // the next tick tries again
        }
      }
    }, intervals.jobs)
    return () => window.clearInterval(timer)
  }, [anyLive, merge])

  const attach = useCallback(
    async (jobId: number) => {
      const job = await getJob(jobId)
      merge(job)
      if (!isLive(job)) finished.current(job) // it may have ended before the first poll
    },
    [merge],
  )

  const resume = useCallback(
    async (jobId: number) => {
      merge(await resumeJob(jobId))
    },
    [merge],
  )

  return { jobs, attach, resume }
}
