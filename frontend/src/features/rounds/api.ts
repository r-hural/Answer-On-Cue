import { apiGet, apiSend, apiUpload } from '../../api/client'

export type FeedbackTiming = 'practice' | 'exam'
export type StyleChoice = 'short' | 'open' | 'mixed'
export type DifficultyChoice = 'recall' | 'apply' | 'analysis' | 'mixed'
export type AudioMode = 'off' | 'premium' | 'browser'
export type ScopeKind = 'subject' | 'topics' | 'documents' | 'chapter'
export type Phase = 'question' | 'preparation' | 'recording' | 'processing' | 'feedback' | 'summary'
/** Whether the phase belongs to the main question or to its follow-up. */
export type Part = 'main' | 'followup'
export type StartReason = 'auto' | 'button' | 'timer'
export type ActionName =
  | 'question_ready'
  | 'start_answer'
  | 'stop'
  | 'dont_know'
  | 'skip'
  | 'mic_failure'
  | 'next'
  | 'pause'
  | 'resume'
  | 'abandon'
  | 'no_follow_up'
  | 'answer_again'

export interface Scope {
  kind: ScopeKind
  topic_ids: number[]
  document_ids: number[]
  /** Kind chapter: the chapter asked; topics/documents: the chapter the round was set up in. */
  chapter_id?: number | null
}

export interface QuestionAudio {
  mode: AudioMode
  provider?: 'piper' | 'azure_speech' | 'openai' | null
  voice?: string | null
  rate?: number | null
}

export interface FollowUpConfig {
  enabled: boolean
  prep_seconds: number
  answer_seconds: number
}

export type RoundType = 'standard' | 'adaptive' | 'extension' | 'retry'

/** End-of-round adaptive extension: N new questions on the round's weak topics. */
export interface ExtensionConfig {
  enabled: boolean
  question_count: number
}

/** A question's generation policy. */
export type SourcePolicy = 'materials' | 'expanded' | 'outside'
/** A round's pool: `all` draws every policy; `expanded` only in rounds from before the sources rework. */
export type RoundSourcePolicy = 'materials' | 'all' | 'expanded'

export interface RoundConfig {
  round_type: RoundType
  scope: Scope
  source_policy: RoundSourcePolicy
  feedback_timing: FeedbackTiming
  question_count: number
  style: StyleChoice
  difficulty: DifficultyChoice
  prep_seconds: number
  answer_seconds: number
  question_audio: QuestionAudio
  follow_ups: FollowUpConfig
  /** Old rounds have none: then it is off. */
  extension?: ExtensionConfig
  /** Old rounds have neither: then both are off. */
  immediate_repeat?: boolean
  retry_round?: boolean
  band_bounds?: [number, number, number] | null
  factual_weights?: [number, number] | null
  /** The subject's content language, recorded at creation; rounds created before it have none. */
  language?: string | null
}

export interface RoundCreate {
  subject_id: number
  /** follow_ups may be left out: the server then keeps follow-ups off. */
  config: Omit<RoundConfig, 'band_bounds' | 'factual_weights' | 'follow_ups'> & { follow_ups?: FollowUpConfig }
}

export interface AttemptOut {
  id: number
  attempt_kind: string
  outcome: string
  processing_status: string | null
  parent_attempt_id: number | null
  start_reason: string | null
  recording_id: number | null
}

export interface ItemOut {
  id: number
  ordinal: number
  role: string
  question_version_id: number
  status: 'pending' | 'current' | 'done'
  latest_attempt: AttemptOut | null
}

export interface CurrentQuestion {
  item_id: number
  question_version_id: number
  text: string
  style: string
  difficulty: string
}

export interface FollowUpFailure {
  kind: string
  message: string
}

/** The current main question's follow-up while it is prepared; never its question or rubric. */
export interface FollowUpState {
  id: number
  status: 'generating' | 'ready' | 'failed' | 'omitted'
  failure: FollowUpFailure | null
  retryable: boolean
}

export interface RoundState {
  id: number
  subject_id: number
  status: string
  phase: Phase
  part: Part
  current_index: number
  total: number
  config: RoundConfig
  current: CurrentQuestion | null
  current_attempt: AttemptOut | null
  items: ItemOut[]
  pause_count: number
  paused_ms: number
  follow_up: FollowUpState | null
  /** Practice feedback may offer one repeat of the main question (old servers: absent). */
  can_answer_again?: boolean
  /** The server's clock for a timed phase or a pause (old servers: absent). */
  clock?: PhaseClock | null
}

/** ISO date-times with an offset, on the server's clock. */
export interface PhaseClock {
  server_now: string
  phase_started_at: string | null
  prep_deadline_at: string | null
  answer_deadline_at: string | null
  paused_at: string | null
}

export interface RoundSummary {
  round_id: number
  status: string
  provisional: boolean
  outcomes: Record<string, number>
  unresolved_attempt_ids: number[]
  interrupted_attempt_ids: number[]
  pause_count: number
  paused_ms: number
}

export interface ActionBody {
  action: ActionName
  from_phase: Phase
  attempt_id?: number
  start_reason?: StartReason
}

/** Times are performance.now() milliseconds from one page clock. */
export interface AttemptEventsIn {
  question_shown_at: number
  question_ready_at: number
  playback_ms: number
  prep_end_at: number
  start_reason: StartReason
  capture_start_at: number
  stopped_at: number
  pre_roll_ms: number
  interrupted: boolean
  listening_available: boolean
}

export type ResultStatus = 'processing' | 'ready' | 'failed'
export type ResultStage =
  | 'waiting_for_audio'
  | 'saved'
  | 'analysing'
  | 'transcribing'
  | 'assessing'
  | 'done'
  | 'not_assessed'

export interface Failure {
  kind: string
  message: string
  retryable: boolean
}

export type Band = 'weak' | 'needs_improvement' | 'good' | 'very_good'

export interface Assessment {
  status: 'assessed' | 'no_answer' | 'no_speech'
  scores: {
    correctness: number
    completeness: number
    factual: number
    language: number | null
    total: number
  } | null
  band: Band | null
}

export interface AttemptResult {
  attempt_id: number
  outcome: string
  status: ResultStatus
  stage: ResultStage
  recording: { id: number; duration_ms: number } | null
  failure: Failure | null
  withheld: boolean
  transcript: string | null
  metrics: Record<string, unknown> | null
  assessment: Assessment | null
}

export const createRound = (body: RoundCreate) => apiSend<RoundState>('POST', '/rounds', body)
export const getRound = (roundId: number) => apiGet<RoundState>(`/rounds/${roundId}`)
export const act = (roundId: number, body: ActionBody) =>
  apiSend<RoundState>('POST', `/rounds/${roundId}/actions`, body)
export const roundSummary = (roundId: number) => apiGet<RoundSummary>(`/rounds/${roundId}/summary`)

export const postEvents = (attemptId: number, body: AttemptEventsIn) =>
  apiSend<{ attempt_id: number; stored: boolean }>('POST', `/attempts/${attemptId}/events`, body)

export function uploadAnswer(attemptId: number, wav: Blob) {
  const form = new FormData()
  form.append('file', wav, 'answer.wav')
  return apiUpload<{ attempt_id: number; recording_id: number; duration_ms: number }>(
    `/attempts/${attemptId}/audio`,
    form,
  )
}

export const getResult = (attemptId: number) => apiGet<AttemptResult>(`/attempts/${attemptId}/result`)
/** The round an attempt belongs to; the outbox checks it before replaying a stored answer. */
export const getAttemptRound = (attemptId: number) =>
  apiGet<{ attempt_id: number; round_id: number }>(`/attempts/${attemptId}/round`)
export const retryAttempt = (attemptId: number) =>
  apiSend<AttemptResult>('POST', `/attempts/${attemptId}/retry`)
/** "Give up this answer": a failed main answer is then not assessed (during or after the round). */
export const giveUpAttempt = (attemptId: number) =>
  apiSend<AttemptResult>('POST', `/attempts/${attemptId}/give-up`)
/** The outcome of an answer that was given up: never scored. */
export const GIVEN_UP = 'given_up'

/** What recovery did for an answer cut off by a reload or a restart. */
export type RecoveryAction = 'none' | 'running' | 'completed' | 'resumed' | 'confirm' | 'waiting_for_audio' | 'failed'

export interface RecoveryOut {
  attempt_id: number
  action: RecoveryAction
  /** The paid call that may already have run (action "confirm"). */
  operation_id: string | null
}

export interface RoundRecoveryOut {
  round_id: number
  attempts: RecoveryOut[]
}

export const recoverAttempt = (attemptId: number) => apiSend<RecoveryOut>('POST', `/attempts/${attemptId}/recover`)
export const recoverRound = (roundId: number) => apiSend<RoundRecoveryOut>('POST', `/rounds/${roundId}/recover`)
export type ExtensionStatus =
  | 'off'
  | 'started'
  | 'ended_early'
  | 'not_finished'
  | 'waiting'
  | 'no_weak'
  | 'ready'
  | 'generating'
  | 'no_questions'

export interface ExtensionOffer {
  round_id: number
  status: ExtensionStatus
  requested: number
  /** New questions the extension would get now, at most `requested`. */
  available: number
  /** The weak answers' topics, weakest first; Polish titles. */
  topics: { topic_id: number; title: string }[]
  job_id: number | null
  extension_round_id: number | null
  extension_status: string | null
  needs_review?: number
}

export const getExtensionOffer = (roundId: number) => apiGet<ExtensionOffer>(`/rounds/${roundId}/extension`)
export const startExtension = (roundId: number) => apiSend<RoundState>('POST', `/rounds/${roundId}/extension`)
export const generateExtensionQuestions = (roundId: number) =>
  apiSend<{ job_id: number }>('POST', `/rounds/${roundId}/extension/generate`)

export const retryFollowUp = (followUpId: number) =>
  apiSend<FollowUpState>('POST', `/follow-ups/${followUpId}/retry`)

export type RetryStatus =
  | 'off'
  | 'started'
  | 'ended_early'
  | 'not_finished'
  | 'waiting'
  | 'extension_open'
  | 'none_below'
  | 'ready'

/** The end-of-round retry round of the answers below Good. */
export interface RetryOffer {
  round_id: number
  status: RetryStatus
  count: number
  /** 1-based: the questions it would ask. */
  ordinals: number[]
  /** Answers that could not be assessed: not in it, tried again from the list. */
  unresolved: number
  retry_round_id: number | null
  retry_status: string | null
}

export const getRetryOffer = (roundId: number) => apiGet<RetryOffer>(`/rounds/${roundId}/retry`)
export const startRetry = (roundId: number) => apiSend<RoundState>('POST', `/rounds/${roundId}/retry`)
