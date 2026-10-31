import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

export interface SubjectWithChapters {
  id: number
  name: string
  /** Missing on an older server: one chapter. */
  chapter_count?: number
}

export interface ChapterRef {
  id: number
  name: string | null
}

/**
 * The chapters of the subject, only when it has two or more (`chapters` is null otherwise, and then
 * nothing is asked). `pending` while they are being loaded; a failed load means no chapter filter.
 */
export function useChapters(subject: SubjectWithChapters | undefined): {
  chapters: ChapterRef[] | null
  pending: boolean
} {
  const [loaded, setLoaded] = useState<{ subjectId: number; list: ChapterRef[] } | null>(null)
  const subjectId = subject?.id ?? null
  const chaptered = (subject?.chapter_count ?? 1) >= 2
  useEffect(() => {
    if (subjectId === null || !chaptered) return
    let cancelled = false
    apiGet<ChapterRef[]>(`/subjects/${subjectId}/chapters`)
      .then((list) => !cancelled && setLoaded({ subjectId, list }))
      .catch(() => !cancelled && setLoaded({ subjectId, list: [] })) // no chapter filter rather than an error
    return () => {
      cancelled = true
    }
  }, [subjectId, chaptered])
  const mine = chaptered && loaded?.subjectId === subjectId ? loaded.list : null
  return { chapters: mine && mine.length >= 2 ? mine : null, pending: chaptered && mine === null }
}
