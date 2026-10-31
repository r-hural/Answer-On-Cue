import type {
  DifficultyChoice,
  FeedbackTiming,
  QuestionAudio,
  RoundCreate,
  RoundSourcePolicy,
  RoundState,
  RoundType,
  ScopeKind,
  StyleChoice,
} from './api'

export type AudioChoice = 'off' | 'piper' | 'azure_speech' | 'browser' | 'openai'

export interface SetupForm {
  subjectId: number | null
  scopeKind: ScopeKind
  topicIds: number[]
  documentIds: number[]
  feedback: FeedbackTiming
  questionCount: number
  style: StyleChoice
  difficulty: DifficultyChoice
  prepSeconds: number
  answerSeconds: number
  audio: AudioChoice
  voice: string
  rate: number
  followUps: boolean
  followUpPrepSeconds: number
  followUpAnswerSeconds: number
  roundType: Exclude<RoundType, 'extension' | 'retry'>
  extension: boolean
  extensionCount: number
  /** "My materials" or "All questions"; `expanded` only in forms stored before the sources rework. */
  sourcePolicy: RoundSourcePolicy
  /** Answer again after each practice feedback; follows the feedback timing while untouched. */
  immediateRepeat: boolean
  retryRound: boolean
  /** The chapter of a subject with two or more chapters; null otherwise (and in forms stored before chapters). */
  chapterId?: number | null
  /** The chapter's active topics, for counting a whole-chapter scope. */
  chapterTopicIds?: number[]
}

/** Ranges of the timers (seconds), as the server checks them. */
export const PREP_RANGE: [number, number] = [0, 600]
export const ANSWER_RANGE: [number, number] = [5, 600]
/** New questions an adaptive extension may ask. */
export const EXTENSION_RANGE: [number, number] = [1, 10]

export function defaultForm(): SetupForm {
  return {
    subjectId: null,
    scopeKind: 'subject',
    topicIds: [],
    documentIds: [],
    feedback: 'practice',
    questionCount: 10,
    style: 'mixed',
    difficulty: 'mixed',
    prepSeconds: 30,
    answerSeconds: 120,
    audio: 'off',
    voice: '',
    rate: 1,
    followUps: true,
    followUpPrepSeconds: 15,
    followUpAnswerSeconds: 60,
    roundType: 'standard',
    extension: false,
    extensionCount: 3,
    sourcePolicy: 'all',
    immediateRepeat: true,
    retryRound: true,
    chapterId: null,
    chapterTopicIds: [],
  }
}

const inRange = (value: number, [min, max]: [number, number]) => Number.isFinite(value) && value >= min && value <= max

/** The timer fields outside their range, by form key (the follow-up ones only when on). */
export function invalidTimers(form: SetupForm): (keyof SetupForm)[] {
  const bad: (keyof SetupForm)[] = []
  if (!inRange(form.prepSeconds, PREP_RANGE)) bad.push('prepSeconds')
  if (!inRange(form.answerSeconds, ANSWER_RANGE)) bad.push('answerSeconds')
  if (form.followUps) {
    if (!inRange(form.followUpPrepSeconds, PREP_RANGE)) bad.push('followUpPrepSeconds')
    if (!inRange(form.followUpAnswerSeconds, ANSWER_RANGE)) bad.push('followUpAnswerSeconds')
  }
  return bad
}

/** The timers of what is being asked now: the follow-up's in the follow-up part. */
export function timersOf(round: Pick<RoundState, 'part' | 'config'>): { prepSeconds: number; answerSeconds: number } {
  if (round.part === 'followup' && round.config.follow_ups) {
    return { prepSeconds: round.config.follow_ups.prep_seconds, answerSeconds: round.config.follow_ups.answer_seconds }
  }
  return { prepSeconds: round.config.prep_seconds, answerSeconds: round.config.answer_seconds }
}

export interface QuestionFacts {
  style: string
  difficulty: string
  primary_topic_id: number | null
  source_policy?: string
}

/** How many questions match the form; documents scope is only known from the server (a 409). */
export function availableCount(questions: QuestionFacts[], form: SetupForm): number {
  return questions.filter((question) => {
    const pool = form.sourcePolicy ?? 'all'
    if (pool !== 'all' && (question.source_policy ?? 'materials') !== pool) return false
    if (form.scopeKind === 'topics') {
      if (question.primary_topic_id === null || !form.topicIds.includes(question.primary_topic_id)) return false
    }
    if (form.scopeKind === 'chapter') {
      const topics = form.chapterTopicIds ?? []
      if (question.primary_topic_id === null || !topics.includes(question.primary_topic_id)) return false
    }
    if (form.style !== 'mixed' && question.style !== form.style) return false
    if (form.difficulty !== 'mixed' && question.difficulty !== form.difficulty) return false
    return true
  }).length
}

export const STORAGE_KEY = 'aoc.practiceForm'

/** The form stored in this tab; a stored "Expand the topic" (before the sources rework) reads as All questions. */
export function readStored(): SetupForm | null {
  try {
    const text = sessionStorage.getItem(STORAGE_KEY)
    if (!text) return null
    const form = { ...defaultForm(), ...JSON.parse(text) } as SetupForm
    return form.sourcePolicy === 'expanded' ? { ...form, sourcePolicy: 'all' } : form
  } catch {
    return null
  }
}

function audioOf(form: SetupForm): QuestionAudio {
  if (form.audio === 'off') return { mode: 'off' }
  if (form.audio === 'browser') return { mode: 'browser' }
  return { mode: 'premium', provider: form.audio, voice: form.voice || null, rate: form.rate }
}

export function toRoundCreate(form: SetupForm): RoundCreate {
  // the chapter goes along for every kind but the whole subject, and only when there is one
  const chapterId = form.scopeKind === 'subject' ? null : (form.chapterId ?? null)
  return {
    subject_id: form.subjectId as number,
    config: {
      round_type: form.roundType,
      scope: {
        kind: form.scopeKind,
        topic_ids: form.scopeKind === 'topics' ? form.topicIds : [],
        document_ids: form.scopeKind === 'documents' ? form.documentIds : [],
        ...(chapterId !== null && { chapter_id: chapterId }),
      },
      source_policy: form.sourcePolicy ?? 'all',
      feedback_timing: form.feedback,
      question_count: form.questionCount,
      style: form.style,
      difficulty: form.difficulty,
      prep_seconds: form.prepSeconds,
      answer_seconds: form.answerSeconds,
      question_audio: audioOf(form),
      follow_ups: {
        enabled: form.followUps,
        prep_seconds: form.followUpPrepSeconds,
        answer_seconds: form.followUpAnswerSeconds,
      },
      extension: { enabled: form.extension, question_count: form.extensionCount },
      // Answer again needs practice feedback: the server refuses it with exam timing
      immediate_repeat: (form.immediateRepeat ?? true) && form.feedback === 'practice',
      retry_round: form.retryRound ?? true,
    },
  }
}

/** A combination the server refuses: "Answer again" checked by the learner with exam feedback.
 * `repeatTouched`: the learner changed the "Answer again" checkbox. */
export function setupConflict(form: SetupForm, repeatTouched: boolean): 'repeat_needs_practice' | null {
  if (repeatTouched && form.immediateRepeat && form.feedback === 'exam') return 'repeat_needs_practice'
  return null
}

/** Whole minutes, with 20 s per question for moving on. */
export function estimateMinutes(count: number, prepSeconds: number, answerSeconds: number): number {
  return Math.ceil((count * (prepSeconds + answerSeconds + 20)) / 60)
}

export type SetupNotice =
  | { kind: 'no-questions' }
  | { kind: 'need-topics' }
  | { kind: 'need-documents' }
  | { kind: 'only'; available: number }
  | { kind: 'error'; message: string }
  | { kind: 'ok' }

export interface NoticeInput {
  form: SetupForm
  /** All active questions of the subject. */
  subjectTotal: number
  /** Matching questions; null when unknown (documents scope). */
  available: number | null
  error: string | null
}

/** Exactly one notice, in the priority (a) empty subject, (b) empty scope, (c) too few, (d) server error. */
export function setupNotice({ form, subjectTotal, available, error }: NoticeInput): SetupNotice {
  if (subjectTotal === 0) return { kind: 'no-questions' }
  if (form.scopeKind === 'topics' && form.topicIds.length === 0) return { kind: 'need-topics' }
  if (form.scopeKind === 'documents' && form.documentIds.length === 0) return { kind: 'need-documents' }
  if (available !== null && available < form.questionCount) return { kind: 'only', available }
  if (error) return { kind: 'error', message: error }
  return { kind: 'ok' }
}
