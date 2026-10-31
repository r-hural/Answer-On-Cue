import { apiGet } from '../../api/client'
import type { Band } from '../rounds/api'

export type CoverageStatus = 'not_practised' | 'needs_work' | 'improving' | 'strong'
/** The questions counted: "My materials" only, or "All questions" (every source policy). */
export type CoverageLayer = 'materials' | 'all'

/** The layer of a URL: `materials`, else All questions (also an old `?layer=expanded` link). */
export const layerOf = (value: string | null): CoverageLayer => (value === 'materials' ? 'materials' : 'all')

export interface Evidence {
  attempt_id: number
  round_id: number
  at: string
  band: Band
  factual: number
  independent: boolean
}

export interface ObjectiveCoverage {
  /** null: the line of questions on the whole topic. */
  objective_id: number | null
  text: string | null
  status: CoverageStatus
  assisted: boolean
  answers: number
  independent_answers: number
  last_practised_at: string | null
  /** Up to 3 independent factual scores, newest first. */
  recent_factual: number[]
  deciding: Evidence | null
  median_preparation_used_ms: number | null
  question_count: number
}

export interface TopicCoverage {
  topic_id: number
  title: string
  status: CoverageStatus
  objectives: ObjectiveCoverage[]
  chapter_id?: number | null
}

export interface SubjectCoverage {
  subject_id: number
  subject_name: string
  line_total: number
  line_counts: Record<CoverageStatus, number>
  topic_counts: Record<CoverageStatus, number>
  topics: TopicCoverage[]
  layer?: CoverageLayer | 'expanded'
  /** The chapter filter applied, if any. */
  chapter_id?: number | null
}

export type HistoryStatus = 'ready' | 'failed' | 'processing' | 'interrupted' | 'not_assessed'

export interface HistoryEntry {
  attempt_id: number
  round_id: number
  at: string
  question_text: string
  style: string
  difficulty: string
  objective_id: number | null
  attempt_kind: string
  independent: boolean
  outcome: string
  status: HistoryStatus
  band: Band | null
  factual: number | null
  language: number | null
  preparation_used_ms: number | null
}

export interface TopicSource {
  passage_id: number
  document_id: number
  document_title: string
  document_version: number
  locator: string
  heading: string | null
  excerpt: string
}

export interface TopicDetail {
  topic_id: number
  subject_id: number
  subject_name: string
  title: string
  description: string | null
  status: CoverageStatus
  objectives: ObjectiveCoverage[]
  question_count: number
  terms: string[]
  passage_count: number
  history: HistoryEntry[]
  sources: TopicSource[]
  layer?: CoverageLayer | 'expanded'
}

// the API default is materials, so only All questions is spelled out
const layerQuery = (layer: CoverageLayer) => (layer === 'all' ? '?layer=all' : '')

/** The layer, plus the chapter filter only when one is given. */
function subjectQuery(layer: CoverageLayer, chapterId?: number | null): string {
  const query = layerQuery(layer)
  if (chapterId == null) return query
  return `${query ? `${query}&` : '?'}chapter_id=${chapterId}`
}

export const getSubjectCoverage = (subjectId: number, layer: CoverageLayer = 'materials', chapterId?: number | null) =>
  apiGet<SubjectCoverage>(`/coverage/subjects/${subjectId}${subjectQuery(layer, chapterId)}`)

export const getTopicCoverage = (topicId: number, layer: CoverageLayer = 'materials') =>
  apiGet<TopicDetail>(`/coverage/topics/${topicId}${layerQuery(layer)}`)
