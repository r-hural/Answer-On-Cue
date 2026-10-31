import { describe, expect, it } from 'vitest'
import { round } from './rounds.mocks'
import {
  availableCount,
  defaultForm,
  estimateMinutes,
  invalidTimers,
  readStored,
  setupConflict,
  setupNotice,
  timersOf,
  toRoundCreate,
  type SetupForm,
} from './setup'

const q = (style: 'short' | 'open', difficulty: 'recall' | 'apply' | 'analysis', topic: number | null) => ({
  style,
  difficulty,
  primary_topic_id: topic,
})
const QUESTIONS = [
  q('short', 'recall', 1),
  q('open', 'apply', 1),
  q('open', 'analysis', 2),
  q('short', 'recall', 3),
  q('open', 'recall', null),
]

function form(patch: Partial<SetupForm> = {}): SetupForm {
  return { ...defaultForm(), subjectId: 4, ...patch }
}

describe('availableCount', () => {
  it('counts every question for the whole subject; mixed means any', () => {
    expect(availableCount(QUESTIONS, form())).toBe(5)
  })

  it('uses primary_topic_id for the topics scope', () => {
    expect(availableCount(QUESTIONS, form({ scopeKind: 'topics', topicIds: [1, 2] }))).toBe(3)
    expect(availableCount(QUESTIONS, form({ scopeKind: 'topics', topicIds: [] }))).toBe(0)
  })

  it('filters by style and difficulty', () => {
    expect(availableCount(QUESTIONS, form({ style: 'short' }))).toBe(2)
    expect(availableCount(QUESTIONS, form({ difficulty: 'recall' }))).toBe(3)
    expect(availableCount(QUESTIONS, form({ style: 'open', difficulty: 'recall' }))).toBe(1)
  })
})

describe('availableCount by source policy', () => {
  const mixedPool = [
    { ...q('short', 'recall', 1), source_policy: 'materials' },
    { ...q('open', 'recall', 1), source_policy: 'expanded' },
    { ...q('open', 'apply', 2), source_policy: 'expanded' },
    q('short', 'apply', 2), // an old client row without the field counts as materials
  ]

  it('counts only the questions of the chosen policy', () => {
    expect(availableCount(mixedPool, form({ sourcePolicy: 'materials' }))).toBe(2)
    expect(availableCount(mixedPool, form({ sourcePolicy: 'expanded' }))).toBe(2)
    expect(availableCount(mixedPool, form({ sourcePolicy: 'expanded', difficulty: 'apply' }))).toBe(1)
  })
})

describe('toRoundCreate', () => {
  it('builds the exact body for the whole subject, audio off', () => {
    expect(toRoundCreate(form())).toEqual({
      subject_id: 4,
      config: {
        round_type: 'standard',
        scope: { kind: 'subject', topic_ids: [], document_ids: [] },
        source_policy: 'all',
        feedback_timing: 'practice',
        question_count: 10,
        style: 'mixed',
        difficulty: 'mixed',
        prep_seconds: 30,
        answer_seconds: 120,
        question_audio: { mode: 'off' },
        follow_ups: { enabled: true, prep_seconds: 15, answer_seconds: 60 },
        extension: { enabled: false, question_count: 3 },
        immediate_repeat: true,
        retry_round: true,
      },
    })
  })

  it('sends the chosen source policy', () => {
    expect(toRoundCreate(form({ sourcePolicy: 'expanded' })).config.source_policy).toBe('expanded')
    expect(defaultForm().sourcePolicy).toBe('all')
  })

  it('sends the round type and the adaptive extension from the form', () => {
    const body = toRoundCreate(form({ roundType: 'adaptive', extension: true, extensionCount: 5 }))
    expect(body.config.round_type).toBe('adaptive')
    expect(body.config.extension).toEqual({ enabled: true, question_count: 5 })
  })

  it('starts Standard with the extension off and 3 questions', () => {
    expect(defaultForm()).toMatchObject({ roundType: 'standard', extension: false, extensionCount: 3 })
  })

  it('sends the follow-up option and its timers', () => {
    const off = toRoundCreate(form({ followUps: false, followUpPrepSeconds: 0, followUpAnswerSeconds: 90 }))
    expect(off.config.follow_ups).toEqual({ enabled: false, prep_seconds: 0, answer_seconds: 90 })
  })

  it('sends ids only for the chosen scope kind', () => {
    const body = toRoundCreate(form({ scopeKind: 'topics', topicIds: [1, 2], documentIds: [9] }))
    expect(body.config.scope).toEqual({ kind: 'topics', topic_ids: [1, 2], document_ids: [] })
    const docs = toRoundCreate(form({ scopeKind: 'documents', topicIds: [1], documentIds: [9] }))
    expect(docs.config.scope).toEqual({ kind: 'documents', topic_ids: [], document_ids: [9] })
  })

  it('maps the audio choices', () => {
    expect(toRoundCreate(form({ audio: 'browser' })).config.question_audio).toEqual({ mode: 'browser' })
    expect(toRoundCreate(form({ audio: 'piper', voice: 'pl_PL-gosia', rate: 1.2 })).config.question_audio).toEqual({
      mode: 'premium',
      provider: 'piper',
      voice: 'pl_PL-gosia',
      rate: 1.2,
    })
    expect(toRoundCreate(form({ audio: 'azure_speech', voice: '', rate: 1 })).config.question_audio).toEqual({
      mode: 'premium',
      provider: 'azure_speech',
      voice: null,
      rate: 1,
    })
  })
})

describe('estimateMinutes', () => {
  it('rounds up N × (prep + answer + 20 s)', () => {
    expect(estimateMinutes(10, 30, 120)).toBe(29)
    expect(estimateMinutes(1, 0, 5)).toBe(1)
  })
})

describe('setupNotice', () => {
  const base = { subjectTotal: 20, available: 20 as number | null, error: null as string | null }

  it('is ok when everything fits', () => {
    expect(setupNotice({ ...base, form: form() })).toEqual({ kind: 'ok' })
  })

  it('(a) an empty subject comes first', () => {
    const notice = setupNotice({ ...base, subjectTotal: 0, available: 0, form: form({ scopeKind: 'topics' }), error: 'x' })
    expect(notice).toEqual({ kind: 'no-questions' })
  })

  it('(b) needs a topic or a document', () => {
    expect(setupNotice({ ...base, form: form({ scopeKind: 'topics' }) })).toEqual({ kind: 'need-topics' })
    expect(setupNotice({ ...base, form: form({ scopeKind: 'documents' }) })).toEqual({ kind: 'need-documents' })
  })

  it('(c) fewer questions match than asked', () => {
    expect(setupNotice({ ...base, available: 4, form: form({ questionCount: 10 }) })).toEqual({
      kind: 'only',
      available: 4,
    })
    expect(setupNotice({ ...base, available: 10, form: form({ questionCount: 10 }) })).toEqual({ kind: 'ok' })
    expect(setupNotice({ ...base, available: null, form: form() })).toEqual({ kind: 'ok' })
  })

  it('(d) shows a server error last', () => {
    expect(setupNotice({ ...base, error: 'Bad voice.', form: form() })).toEqual({ kind: 'error', message: 'Bad voice.' })
    expect(setupNotice({ ...base, error: 'Bad voice.', available: 3, form: form() })).toEqual({
      kind: 'only',
      available: 3,
    })
  })
})

describe('follow-ups', () => {
  it('are on by default with 15 s / 60 s', () => {
    expect(defaultForm()).toMatchObject({ followUps: true, followUpPrepSeconds: 15, followUpAnswerSeconds: 60 })
  })

  it('names the timer fields out of range, the follow-up ones only when on', () => {
    expect(invalidTimers(form())).toEqual([])
    expect(invalidTimers(form({ followUpPrepSeconds: 0, followUpAnswerSeconds: 5 }))).toEqual([])
    expect(invalidTimers(form({ followUpPrepSeconds: -1, followUpAnswerSeconds: 4 }))).toEqual([
      'followUpPrepSeconds',
      'followUpAnswerSeconds',
    ])
    expect(invalidTimers(form({ followUpPrepSeconds: 601, answerSeconds: 4 }))).toEqual(['answerSeconds', 'followUpPrepSeconds'])
    expect(invalidTimers(form({ followUps: false, followUpAnswerSeconds: 4 }))).toEqual([])
  })

  it('timersOf gives the follow-up timers in the follow-up part', () => {
    const state = round()
    state.config.follow_ups = { enabled: true, prep_seconds: 15, answer_seconds: 60 }
    expect(timersOf(state)).toEqual({ prepSeconds: 30, answerSeconds: 120 })
    expect(timersOf({ ...state, part: 'followup' })).toEqual({ prepSeconds: 15, answerSeconds: 60 })
  })
})

describe('answer again and the retry round', () => {
  it('are both on in the default form', () => {
    expect(defaultForm()).toMatchObject({ immediateRepeat: true, retryRound: true })
  })

  it('toRoundCreate sends both options', () => {
    expect(toRoundCreate(form({ immediateRepeat: false, retryRound: true })).config).toMatchObject({
      immediate_repeat: false,
      retry_round: true,
    })
    expect(toRoundCreate(form({ retryRound: false })).config).toMatchObject({ immediate_repeat: true, retry_round: false })
  })

  it('never sends Answer again with exam feedback', () => {
    const body = toRoundCreate(form({ feedback: 'exam' }))
    expect(body.config).toMatchObject({ feedback_timing: 'exam', immediate_repeat: false, retry_round: true })
  })

  it('setupConflict only for a checked, touched Answer again with Exam', () => {
    const touched = true
    const none = false
    expect(setupConflict(form({ feedback: 'exam', immediateRepeat: true }), touched)).toBe('repeat_needs_practice')
    expect(setupConflict(form({ feedback: 'exam', immediateRepeat: true }), none)).toBeNull()
    expect(setupConflict(form({ feedback: 'exam', immediateRepeat: false }), touched)).toBeNull()
    expect(setupConflict(form({ feedback: 'practice', immediateRepeat: true }), touched)).toBeNull()
  })
})

describe('chapters', () => {
  it('sends kind chapter with its chapter_id', () => {
    expect(toRoundCreate(form({ scopeKind: 'chapter', chapterId: 7 })).config.scope).toEqual({
      kind: 'chapter',
      topic_ids: [],
      document_ids: [],
      chapter_id: 7,
    })
  })

  it('adds chapter_id to topics and documents scopes, never to the whole subject', () => {
    expect(toRoundCreate(form({ scopeKind: 'topics', topicIds: [1], chapterId: 7 })).config.scope).toEqual({
      kind: 'topics',
      topic_ids: [1],
      document_ids: [],
      chapter_id: 7,
    })
    expect(toRoundCreate(form({ scopeKind: 'documents', documentIds: [3], chapterId: 7 })).config.scope).toEqual({
      kind: 'documents',
      topic_ids: [],
      document_ids: [3],
      chapter_id: 7,
    })
    expect(toRoundCreate(form({ scopeKind: 'subject', chapterId: 7 })).config.scope).toEqual({
      kind: 'subject',
      topic_ids: [],
      document_ids: [],
    })
  })

  it('without a chapter the body is exactly as before', () => {
    expect(toRoundCreate(form({ scopeKind: 'topics', topicIds: [1] })).config.scope).toEqual({
      kind: 'topics',
      topic_ids: [1],
      document_ids: [],
    })
  })

  it('counts a whole chapter by the primary topics of its questions', () => {
    expect(availableCount(QUESTIONS, form({ scopeKind: 'chapter', chapterId: 7, chapterTopicIds: [1, 3] }))).toBe(3)
    expect(availableCount(QUESTIONS, form({ scopeKind: 'chapter', chapterId: 7, chapterTopicIds: [] }))).toBe(0)
  })

  it('accepts a form stored before chapters', () => {
    const old: Partial<SetupForm> = { ...defaultForm() }
    delete old.chapterId
    delete old.chapterTopicIds
    const restored: SetupForm = { ...defaultForm(), ...old, subjectId: 4 }
    expect(restored.chapterId).toBeNull()
    expect(toRoundCreate(restored).config.scope).toEqual({ kind: 'subject', topic_ids: [], document_ids: [] })
  })
})


describe('practice sources', () => {
  const pool = [
    { ...q('short', 'recall', 1), source_policy: 'materials' },
    { ...q('open', 'recall', 1), source_policy: 'expanded' },
    { ...q('open', 'apply', 2), source_policy: 'outside' },
    q('short', 'apply', 2),
  ]

  it('opens on All questions', () => {
    expect(defaultForm().sourcePolicy).toBe('all')
  })

  it('counts every policy under All questions and only materials under My materials', () => {
    expect(availableCount(pool, form({ sourcePolicy: 'all' }))).toBe(4)
    expect(availableCount(pool, form({ sourcePolicy: 'materials' }))).toBe(2)
  })

  it('reads a stored Expand the topic as All questions', () => {
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify({ sourcePolicy: 'expanded', questionCount: 4 }))
    try {
      expect(readStored()).toMatchObject({ sourcePolicy: 'all', questionCount: 4 })
      sessionStorage.setItem('aoc.practiceForm', JSON.stringify({ sourcePolicy: 'materials' }))
      expect(readStored()?.sourcePolicy).toBe('materials')
    } finally {
      sessionStorage.clear()
    }
  })

  it('always sends source_policy', () => {
    expect(toRoundCreate(form()).config.source_policy).toBe('all')
    expect(toRoundCreate(form({ sourcePolicy: 'materials' })).config.source_policy).toBe('materials')
  })
})
