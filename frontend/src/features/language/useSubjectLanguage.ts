import { useEffect, useState } from 'react'
import { getSubject } from '../library/api'

/** A subject's language code; null while loading, on an error and without an id (the provider then shows Polish). */
export function useSubjectLanguage(subjectId: number | null | undefined): string | null {
  const [loaded, setLoaded] = useState<{ id: number; code: string } | null>(null)

  useEffect(() => {
    if (subjectId === null || subjectId === undefined) return
    let cancelled = false
    getSubject(subjectId).then(
      (subject) => {
        if (!cancelled) setLoaded({ id: subjectId, code: subject.language ?? 'pl' })
      },
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [subjectId])

  return loaded !== null && loaded.id === subjectId ? loaded.code : null
}
