import { apiGet, apiSend } from '../../api/client'

export type Basis = 'typical' | 'measured'
export type UsageGroup = 'operation' | 'round' | 'subject' | 'month'

export interface MonthState {
  month: string
  ceiling_usd: number
  spent_usd: number
  reserved_usd: number
  remaining_usd: number | null
}

export interface EstimatePart {
  operation: string
  usd: number
}

export interface RoundEstimate {
  low_usd: number
  high_usd: number
  transcription_minutes: number
  basis: Basis
  parts: EstimatePart[]
  voice_provider: string
  month: MonthState
  fits: boolean
}

export interface JobEstimate {
  kind: string
  usd: number
  questions: number
  sections: number
  minutes: number
  provider: string
  basis: Basis
  month: MonthState
  fits: boolean
}

export interface UsageRow {
  key: string
  label: string | null
  provider: string | null
  model: string | null
  price_known: boolean
  requests: number
  blocked: number
  input_tokens: number
  output_tokens: number
  audio_minutes: number
  characters: number
  estimated_usd: number | null
  reported_usd: number | null
}

export interface UsageTable {
  month: string
  group: UsageGroup
  rows: UsageRow[]
}

export interface MonthSummary {
  month: MonthState
  rounds: number
  per_round_usd: number | null
  library_usd: number
  voice_usd: number
  voice_provider: string
  blocked: number
  months: string[]
}

export interface PriceRow {
  provider: string
  model: string
  role: string
  input_per_m: number
  output_per_m: number
  audio_per_min: number
  chars_per_m: number
  checked: string
}

export interface RoundEstimateQuery {
  subjectId: number
  questionCount: number
  answerSeconds: number
  followUps: boolean
}

/** The body of POST /api/questions/generate: the estimate prices exactly that job. */
export interface GenerationBody {
  subject_id: number
  per_topic: number
  topic_ids?: number[] | null
  style?: string
  difficulty?: string
  source_policy?: string
  chapter_id?: number | null
  document_version_id?: number | null
}

const monthQuery = (month?: string) => (month ? `?month=${encodeURIComponent(month)}` : '')

export const fetchSummary = (month?: string) =>
  apiGet<MonthSummary>(`/costs/summary${monthQuery(month)}`)

export function fetchUsage(group: UsageGroup, month?: string) {
  const params = new URLSearchParams({ group })
  if (month) params.set('month', month)
  return apiGet<UsageTable>(`/costs/usage?${params}`)
}

export function fetchRoundEstimate(query: RoundEstimateQuery) {
  const params = new URLSearchParams({
    subject_id: String(query.subjectId),
    question_count: String(query.questionCount),
    answer_seconds: String(query.answerSeconds),
    follow_ups: String(query.followUps),
  })
  return apiGet<RoundEstimate>(`/costs/estimate/round?${params}`)
}

export const fetchGenerationEstimate = (body: GenerationBody) =>
  apiSend<JobEstimate>('POST', '/costs/estimate/generation', body)

export const fetchExtractionEstimate = (documentVersionId: number) =>
  apiGet<JobEstimate>(`/costs/estimate/extraction?document_version_id=${documentVersionId}`)

export const fetchPrices = () => apiGet<PriceRow[]>('/costs/prices')
