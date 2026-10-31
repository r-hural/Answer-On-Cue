import { apiGet, apiSend } from '../../api/client'
import type { AttemptResult, Band, FeedbackTiming, Failure, RoundConfig, RoundSourcePolicy } from './api'

export type PointStatus = 'covered' | 'partial' | 'missing' | 'wrong'
export type RegisterKey = 'grammar' | 'vocabulary' | 'formality' | 'terminology' | 'structure' | 'precision'
export type ModelAnswerDisplay = 'auto' | 'one_click' | 'on_request'

export interface PauseMarker {
  start_ms: number
  end_ms: number
  kind: 'long' | 'very_long'
}

/** The answer's measurements; a null value is "unavailable" (see `unavailable` for the reason). */
export type AnswerMetrics = {
  preparation_used_ms: number
  speech_start_latency_ms: number | null
  total_response_latency_ms: number | null
  answer_duration_ms: number
  first_to_last_speech_ms: number | null
  speech_ms: number
  leading_silence_ms: number | null
  trailing_silence_ms: number | null
  internal_silence_ms: number | null
  silence_ratio: number | null
  longest_internal_silence_ms: number | null
  long_pause_count: number | null
  very_long_pause_count: number | null
  long_pauses_per_min: number | null
  words: number | null
  words_per_minute: number | null
  filler_count: number | null
  filler_per_min: number | null
  filler_examples: string[]
  fillers_lower_bound: boolean
  pauses: PauseMarker[]
  flags: 'slow_start'[]
  unavailable: Record<string, string>
  approximate: boolean
  interrupted: boolean
  detector: string
}

export interface PointJudgement {
  point_id: string
  status: PointStatus
  evidence: string | null
  note: string
}

export interface FactualMistake {
  quote: string
  correction: string
  major: boolean
  rule_index: number | null
}

export interface OutsideMaterial {
  quote: string
  judgement: 'valid' | 'doubtful' | 'wrong'
  note: string
}

export interface RegisterJudgement {
  summary: string
  quotes: string[]
}

export interface LanguageIssue {
  dimension: RegisterKey
  quote: string
  suggestion: string
  explanation: string
}

export interface TranscriptionDoubt {
  quote: string
  likely_meant: string | null
  reason: string
}

export interface AiAssessment {
  correctness: number
  correctness_evidence: string[]
  points: PointJudgement[]
  factual_mistakes: FactualMistake[]
  enrichment_mentioned: string[]
  outside_material: OutsideMaterial[]
  language: number | null
  language_register: Record<RegisterKey, RegisterJudgement>
  language_issues: LanguageIssue[]
  better_version: string
  improvements: string[]
  fluency: number | null
  fluency_coverage: 'full' | 'partial' | 'none'
  fluency_confidence: 'low' | 'medium' | 'high'
  fluency_feedback: string
  hesitation_examples: string[]
  transcription_doubts: TranscriptionDoubt[]
  uncertainty_flags: string[]
}

export interface FullAssessment {
  status: 'assessed' | 'no_answer' | 'no_speech'
  scores: {
    correctness: number
    completeness: number
    factual: number
    language: number | null
    total: number
  } | null
  band: Band | null
  model_answer_display: ModelAnswerDisplay | null
  ai: AiAssessment | null
  band_bounds: [number, number, number]
  factual_weights: [number, number]
}

/** What GET /attempts/:id/result returns, with the full metrics and assessment typed. */
export type ReportResult = Omit<AttemptResult, 'metrics' | 'assessment'> & {
  metrics: AnswerMetrics | null
  assessment: FullAssessment | null
}

export interface PointOut {
  id: string
  text: string
  status: PointStatus
  evidence: string | null
  note: string
  /** Source policy: a point beyond the materials is labelled. */
  basis?: 'source' | 'expanded' | 'mixed'
  /** The sources disagree on this point: shown in source_conflicts, not graded unless all are. */
  disputed?: boolean
}

export interface SourceOut {
  passage_id: number
  document_title: string
  locator: string
  heading: string | null
  excerpt: string
}

export interface TranscriptReportOut {
  attempt_id: number
  corrected_text: string
  created_at: string
}

export type FollowUpReason = 'no_answer' | 'main_unresolved' | 'generation_failed' | 'continued' | 'not_reached'

/** A main answer's follow-up: asked (with its own scores) or why not. */
export interface FollowUpBrief {
  asked: boolean
  reason: FollowUpReason | null
  failure_message: string | null
  question_text: string | null
  attempt_id: number | null
  outcome: string | null
  status: 'ready' | 'failed' | 'processing' | null
  band: Band | null
  factual: number | null
  note: string | null
}

export interface AttemptReport {
  attempt_id: number
  round_id: number
  subject_id: number
  subject_name: string
  ordinal: number
  total: number
  feedback_timing: FeedbackTiming
  round_status: string
  question_text: string
  result: ReportResult
  points: PointOut[]
  key_points: string[]
  model_answer: string | null
  expanded_explanation: string | null
  model_answer_display: ModelAnswerDisplay | null
  sources: SourceOut[]
  transcript_report: TranscriptReportOut | null
  role?: 'main' | 'followup'
  /** A follow-up's main attempt. */
  parent_attempt_id?: number | null
  /** Main answers of rounds with follow-ups on. */
  follow_up?: FollowUpBrief | null
  /** Old servers leave it out: Standard. */
  round_type?: string
  /** The question's generation policy. */
  source_policy?: 'materials' | 'expanded' | 'outside'
  question_basis?: 'source' | 'expanded' | 'mixed'
  /** Model-answer details beyond the materials (Polish); only with the model answer. */
  beyond_material?: string[]
  source_conflicts?: ConflictOut[]
  /** initial, immediate (Answer again), retry, followup; old servers leave it out. */
  attempt_kind?: string
  /** A repeat's or retry's original answer. */
  original?: AnswerBrief | null
  /** An original's repeat (Answer again). */
  again_attempt_id?: number | null
}

/** One answer in a comparison: an original next to its repeat or retry. */
export interface AnswerBrief {
  attempt_id: number | null
  outcome: string
  status: ItemStatus
  band: Band | null
  factual: number | null
  total: number | null
  preparation_used_ms: number | null
  long_pause_count: number | null
  fluency: number | null
}

export interface SeriesRow {
  ordinal: number
  item_id: number
  question_text: string
  original: AnswerBrief
  again: AnswerBrief
  factual_change: number | null
  below_good: boolean
}

/** The round's Answer again repeats: with help, never part of the round score. */
export interface RepeatSeries {
  asked: number
  good_or_better: number
  improved: number
  rows: SeriesRow[]
  knowledge: RoundKnowledgeSummary
  delivery: RoundDeliverySummary | null
}

/** The round's retry round, on the original's summary; its own score. */
export interface RetrySeries {
  round_id: number
  status: string
  started_at: string
  asked: number
  total: number
  good_or_better: number
  still_below: number
  provisional: boolean
  withheld: boolean
  knowledge: RoundKnowledgeSummary | null
}

/** A retry round against its original round's answers to the same questions. */
export interface RetryComparison {
  parent_round_id: number
  parent_started_at: string
  paired: number
  original_mean_total: number | null
  retry_mean_total: number | null
  original_mean_factual: number | null
  retry_mean_factual: number | null
  original_median_preparation_ms: number | null
  retry_median_preparation_ms: number | null
  rows: SeriesRow[]
  /** Ordinals of the answers still below Good. */
  still_below: number[]
}

/** A point on which the sources disagree, with both passages. */
export interface ConflictOut {
  point_id: string | null
  point_text: string | null
  note: string
  sources: SourceOut[]
}

export type ItemStatus = 'ready' | 'failed' | 'processing' | 'not_asked'

export interface ItemRow {
  ordinal: number
  item_id: number
  attempt_id: number | null
  question_text: string
  outcome: string
  status: ItemStatus
  band: Band | null
  factual: number | null
  total: number | null
  preparation_used_ms: number | null
  speech_start_latency_ms: number | null
  auto_start: boolean
  fluency: number | null
  restarted: boolean
  failure: Failure | null
  /** The item's repeat (Answer again), apart from the score. */
  again?: AnswerBrief | null
}

export interface RoundKnowledgeSummary {
  primary_score: number | null
  mean_correctness: number | null
  mean_completeness: number | null
  mean_factual: number | null
  mean_language: number | null
  language_assessable: number
  mean_fluency: number | null
  fluency_assessable: number
  band_distribution: Record<Band, number>
  assessed: number
  no_answer: number
  no_speech: number
  unresolved: number
  /** Given-up answers: left out of the score. Old servers leave it out. */
  not_assessed?: number
  provisional: boolean
}

export interface RoundDeliverySummary {
  count_included: number
  count_excluded_interrupted: number
  count_excluded_other_kind: number
  median_preparation_used_ms: number | null
  median_speech_start_latency_ms: number | null
  median_total_response_latency_ms: number | null
  median_total_response_latency_successful_ms: number | null
  target_attainment: Record<string, [number, number]>
  median_longest_pause_ms: number | null
  long_pauses_per_speaking_min: number | null
  aggregate_silence_ratio: number | null
  median_words_per_minute: number | null
  filler_per_min: number | null
}

/** The follow-ups' own scores; never part of the round score. */
export interface FollowUpSeries {
  asked: number
  not_asked: number
  unresolved: number
  good_or_better: number
  provisional: boolean
  knowledge: RoundKnowledgeSummary
  /** Asked follow-ups whose answer is still being processed (absent from older servers). */
  processing?: number
}

export interface RoundReport {
  round_id: number
  subject_id: number
  subject_name: string
  status: string
  started_at: string
  finished_at: string | null
  config: RoundConfig
  withheld: boolean
  provisional: boolean
  asked: number
  total: number
  knowledge: RoundKnowledgeSummary | null
  delivery: RoundDeliverySummary | null
  items: ItemRow[]
  pause_count: number
  paused_ms: number
  restarted_ordinals: number[]
  /** null when follow-ups were off or the scores are withheld. */
  follow_ups?: FollowUpSeries | null
  /** An adaptive extension's round (absent from older servers). */
  parent_round_id?: number | null
  /** Adaptive rounds only: why the questions were chosen, by category. */
  selection_counts?: Record<string, number> | null
  /** The round's adaptive extension, once started; never part of this round's scores. */
  extension?: ExtensionSeries | null
  /** The round's Answer again repeats. */
  repeats?: RepeatSeries | null
  /** The round's retry round, once started. */
  retry?: RetrySeries | null
  /** A retry round against its original round. */
  retry_comparison?: RetryComparison | null
  ai_cost_usd?: number | null
}

/** The adaptive extension's own scores, read with its round's report. */
export interface ExtensionSeries {
  round_id: number
  status: string
  asked: number
  total: number
  good_or_better: number
  provisional: boolean
  withheld: boolean
  knowledge: RoundKnowledgeSummary | null
}

export interface HistoryRow {
  round_id: number
  subject_id: number
  subject_name: string
  status: string
  started_at: string
  finished_at: string | null
  feedback_timing: FeedbackTiming
  total: number
  asked: number
  primary_score: number | null
  provisional: boolean
  band_distribution: Record<Band, number>
  median_preparation_used_ms: number | null
  mean_fluency: number | null
  withheld: boolean
  /** Old servers leave it out: Standard. History never lists an adaptive extension. */
  round_type?: string
  /** The raw stored value: `expanded` only for rounds from before the sources rework. */
  source_policy?: RoundSourcePolicy
  /** A retry round's original round. */
  parent_round_id?: number | null
  parent_started_at?: string | null
  /** The chapter the round was set up in; null for whole-subject rounds and rounds from before chapters. */
  chapter_id?: number | null
  /** null for the unnamed first chapter. */
  chapter_name?: string | null
  ai_cost_usd?: number | null
}

export interface HistoryFilter {
  subjectId?: number | null
  feedbackTiming?: FeedbackTiming | null
  sourcePolicy?: RoundSourcePolicy | null
  chapterId?: number | null
}

export const getAttemptReport = (attemptId: number) => apiGet<AttemptReport>(`/reports/attempts/${attemptId}`)

export const putTranscriptReport = (attemptId: number, correctedText: string) =>
  apiSend<TranscriptReportOut>('PUT', `/reports/attempts/${attemptId}/transcript-report`, { corrected_text: correctedText })

export const getRoundReport = (roundId: number) => apiGet<RoundReport>(`/reports/rounds/${roundId}`)

export function getHistory(filter: HistoryFilter = {}) {
  const query = new URLSearchParams()
  if (filter.subjectId != null) query.set('subject_id', String(filter.subjectId))
  if (filter.feedbackTiming != null) query.set('feedback_timing', filter.feedbackTiming)
  if (filter.sourcePolicy != null) query.set('source_policy', filter.sourcePolicy)
  if (filter.chapterId != null) query.set('chapter_id', String(filter.chapterId))
  const text = query.toString()
  return apiGet<HistoryRow[]>(`/reports/history${text ? `?${text}` : ''}`)
}
