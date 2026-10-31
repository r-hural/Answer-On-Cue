import { describe, expect, it } from 'vitest'
import { awaitsQuestions, chapterLabel, defaultBookChoice, isChaptered } from './chapters'
import { doc, version } from './library.mocks'

describe('chapterLabel', () => {
  it('gives the name, or Unnamed chapter for the unnamed first chapter', () => {
    expect(chapterLabel({ name: 'Rewolucja francuska' })).toBe('Rewolucja francuska')
    expect(chapterLabel({ name: null })).toBe('Unnamed chapter')
  })
})

describe('isChaptered', () => {
  it('is true only from two chapters on; a missing count is one chapter', () => {
    expect(isChaptered({ chapter_count: 2 })).toBe(true)
    expect(isChaptered({ chapter_count: 3 })).toBe(true)
    expect(isChaptered({ chapter_count: 1 })).toBe(false)
    expect(isChaptered({})).toBe(false)
    expect(isChaptered(null)).toBe(false)
  })
})

describe('defaultBookChoice', () => {
  const ready = (id: number, chapter: number, patch = {}) =>
    doc({
      id,
      title: `Książka ${id}`,
      chapter_id: chapter,
      latest_version: version({
        id: id * 10,
        topics_extracted_at: '2026-10-24T10:00:00',
        questions_generated_at: null,
        topic_count: 3,
        ...patch,
      }),
    })

  it('picks the newest ready version in the chapter', () => {
    const docs = [ready(1, 1), ready(2, 1), ready(3, 2)]
    expect(defaultBookChoice(docs, 1)?.id).toBe(2)
    expect(defaultBookChoice(docs, 2)?.id).toBe(3)
    expect(defaultBookChoice(docs, null)?.id).toBe(3)
  })

  it('skips books with questions, without topics, not extracted or not imported', () => {
    expect(defaultBookChoice([ready(1, 1, { questions_generated_at: '2026-10-24T11:00:00' })], 1)).toBeNull()
    expect(defaultBookChoice([ready(1, 1, { topic_count: 0 })], 1)).toBeNull()
    expect(defaultBookChoice([ready(1, 1, { topics_extracted_at: null })], 1)).toBeNull()
    expect(defaultBookChoice([ready(1, 1, { status: 'failed' })], 1)).toBeNull()
    expect(defaultBookChoice([], 1)).toBeNull()
  })

  it('treats missing fields as unknown', () => {
    expect(defaultBookChoice([doc({ chapter_id: 1 })], 1)).toBeNull()
    expect(defaultBookChoice([ready(1, 1, { questions_generated_at: undefined })], 1)).toBeNull()
    expect(defaultBookChoice([ready(1, 1, { topic_count: undefined })], 1)).toBeNull()
  })
})

describe('awaitsQuestions after a replaced document', () => {
  const book = (patch = {}) =>
    doc({
      latest_version: version({ topics_extracted_at: '2026-10-24T10:00:00', topic_count: 2, ...patch }),
    })

  it('is true when the questions are older than the topics', () => {
    expect(awaitsQuestions(book({ questions_generated_at: '2026-10-23T09:00:00' }))).toBe(true)
  })

  it('is false when the questions are newer than the topics', () => {
    expect(awaitsQuestions(book({ questions_generated_at: '2026-10-24T11:00:00' }))).toBe(false)
  })

  it('keeps the earlier cases', () => {
    expect(awaitsQuestions(book({ questions_generated_at: null }))).toBe(true)
    expect(awaitsQuestions(book({ questions_generated_at: null, topic_count: 0 }))).toBe(false)
    expect(awaitsQuestions(book({ questions_generated_at: undefined }))).toBe(false)
    expect(awaitsQuestions(book({ topics_extracted_at: null, questions_generated_at: null }))).toBe(false)
  })
})
