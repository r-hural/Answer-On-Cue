import type { Chapter, Document, Subject } from './api'
import { libraryStrings } from './strings'

/** The chapter's name, or the English "Unnamed chapter" for the first chapter while it has none. */
export function chapterLabel(chapter: Pick<Chapter, 'name'>): string {
  return chapter.name ?? libraryStrings.chapters.unnamed
}

/** Chapters show only from the second one on; an older server sends no count: one chapter. */
export function isChaptered(subject: Pick<Subject, 'chapter_count'> | null | undefined): boolean {
  return (subject?.chapter_count ?? 1) >= 2
}

/** Documents of the chapter; without a chapter (one-chapter subject) all of them. */
export function documentsIn(documents: Document[], chapterId: number | null): Document[] {
  return chapterId === null ? documents : documents.filter((doc) => doc.chapter_id === chapterId)
}

/** An imported version whose topics were extracted, so "Generate for" can offer it. */
export function hasExtractedTopics(doc: Document): boolean {
  return doc.latest_version.status === 'imported' && typeof doc.latest_version.topics_extracted_at === 'string'
}

/**
 * Extracted topics, at least one of them, and no questions generated since the topics were extracted
 * (for this version or, after a replace, an earlier one).
 */
export function awaitsQuestions(doc: Document): boolean {
  const version = doc.latest_version
  const generated = version.questions_generated_at
  const since =
    generated === null ||
    (typeof generated === 'string' && Date.parse(generated) < Date.parse(version.topics_extracted_at as string))
  return hasExtractedTopics(doc) && since && (version.topic_count ?? 0) > 0
}

/**
 * The book whose topics are ready but which has no questions yet: the newest imported version in the
 * chapter with extracted topics, no generated questions and at least one topic. Missing fields (an
 * older server) mean "unknown" and never count.
 */
export function defaultBookChoice(documents: Document[], chapterId: number | null): Document | null {
  const ready = documentsIn(documents, chapterId).filter(awaitsQuestions)
  if (ready.length === 0) return null
  return ready.reduce((newest, doc) => (doc.latest_version.id > newest.latest_version.id ? doc : newest))
}
