import { apiGet } from '../../api/client'

export type TrendSeries = 'main' | 'followup' | 'immediate' | 'retry' | 'extension'
/** "My materials" or "All questions" (which also holds the expanded rounds from before the sources rework). */
export type TrendSources = 'materials' | 'all'
export type TrendStyle = 'short' | 'open'
export type TrendDifficulty = 'recall' | 'apply' | 'analysis'
export type TrendChange = 'band_table' | 'model' | 'analysis'

export interface TrendPoint {
  round_id: number
  started_at: string
  status: 'finished' | 'abandoned'
  round_type: string
  feedback_timing: string
  answers: number
  unresolved: number
  assisted: number
  restarted: number
  provisional: boolean
  primary: number | null
  factual: number | null
  language: number | null
  language_assessable: number
  speed_answers: number
  median_preparation_used_ms: number | null
  median_speech_start_latency_ms: number | null
  median_total_response_latency_ms: number | null
  median_total_response_latency_successful_ms: number | null
  long_pauses_per_min: number | null
  silence_ratio: number | null
  filler_per_min: number | null
  filler_reliable: number
  fluency: number | null
  fluency_assessable: number
  band_bounds: [number, number, number]
  changes: TrendChange[]
}

export interface TrendFilterOut {
  subject_id: number
  source_policy: TrendSources | 'expanded'
  series: TrendSeries
  style: TrendStyle | null
  difficulty: TrendDifficulty | null
  include_interrupted: boolean
  include_assisted: boolean
}

export interface TrendsOut {
  subject_id: number
  subject_name: string
  filters: TrendFilterOut
  band_bounds: [number, number, number]
  points: TrendPoint[]
}

export interface TrendFilter {
  subjectId: number
  /** `expanded` still asks for the legacy expanded rounds alone (the API keeps it). */
  sources?: TrendSources | 'expanded'
  series?: TrendSeries
  style?: TrendStyle | null
  difficulty?: TrendDifficulty | null
  interrupted?: boolean
  assisted?: boolean
}

/** The query of a filter; defaults are left out. */
export function trendsQuery(filter: TrendFilter): string {
  const query = new URLSearchParams({ subject_id: String(filter.subjectId) })
  if (filter.sources && filter.sources !== 'materials') query.set('source_policy', filter.sources)
  if (filter.series && filter.series !== 'main') query.set('series', filter.series)
  if (filter.style) query.set('style', filter.style)
  if (filter.difficulty) query.set('difficulty', filter.difficulty)
  if (filter.interrupted) query.set('include_interrupted', 'true')
  if (filter.assisted) query.set('include_assisted', 'true')
  return query.toString()
}

export const getTrends = (filter: TrendFilter) => apiGet<TrendsOut>(`/trends?${trendsQuery(filter)}`)
