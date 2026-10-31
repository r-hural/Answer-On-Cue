import { apiGet, apiSend } from '../../api/client'

export type Verdict = 'ok' | 'weak' | 'bad'
export type Basis = 'source' | 'expanded' | 'mixed'

export interface SourceIssue {
  kind: 'contradiction' | 'unclear' | 'insufficient'
  passage_ids: number[]
  note: string
}

export interface EssentialPoint {
  id: string
  text: string
  weight: number
  alternatives: string[]
  passage_ids: number[]
  basis?: Basis
}

export interface Rubric {
  essential_points: EssentialPoint[]
  enrichment_points: unknown[]
  misconceptions: string[]
  major_error_rules: string[]
  model_answer: string
  expanded_explanation: string | null
  beyond_material?: string[]
  source_issues?: SourceIssue[]
}

export interface PassageRef {
  id: number
  locator: string
  heading: string | null
  text: string
}

export interface Question {
  version_id: number
  family_id: number
  version: number
  text: string
  style: 'short' | 'open'
  difficulty: 'recall' | 'apply' | 'analysis'
  primary_topic_id: number | null
  status: string
  rubric: Rubric
  passages: PassageRef[]
  review_note: string | null
  source_policy?: string
  basis?: Basis
  /** Cited passages changed or removed in a newer version of their document. */
  source_changes?: SourceChange[]
  /** "ai" | "user" (edited) | "kept" (kept after a document change). */
  origin?: string
}

export interface SourceChange {
  document_id: number
  document_title: string
  old_version: number
  new_version: number
  old: PassageRef
  /** null: the passage was removed. */
  new: PassageRef | null
}

export interface Review {
  question_version_id: number
  verdict: Verdict
  note: string | null
}

export interface TopicRef {
  id: number
  title: string
}

export interface QuestionEditBody {
  text: string
  style: string
  difficulty: string
  rubric: Rubric
}

export const getSubject = (id: number) => apiGet<{ id: number; name: string; language?: string }>(`/subjects/${id}`)
export const listTopics = (subjectId: number) => apiGet<TopicRef[]>(`/topics?subject_id=${subjectId}`)
export const listQuestionIds = async (subjectId: number) =>
  (
    await apiGet<{ version_id: number }[]>(
      `/questions?subject_id=${subjectId}&status=active&status=needs_review`,
    )
  ).map((question) => question.version_id)
export const getQuestion = (versionId: number) => apiGet<Question>(`/questions/${versionId}`)
export const listReviews = (subjectId: number) =>
  apiGet<Review[]>(`/questions/reviews?subject_id=${subjectId}`)
export const putReview = (versionId: number, verdict: Verdict, note: string | null) =>
  apiSend<Review>('PUT', `/questions/${versionId}/review`, { verdict, note })
export const editQuestion = (versionId: number, body: QuestionEditBody) =>
  apiSend<Question>('PUT', `/questions/${versionId}`, body)
export const retireQuestion = (versionId: number) =>
  apiSend<unknown>('POST', `/questions/${versionId}/retire`)
export const exportUrl = (subjectId: number) => `/api/questions/reviews/export?subject_id=${subjectId}`
export const keepQuestion = (versionId: number) => apiSend<Question>('POST', `/questions/${versionId}/keep`)
export const checkQuestion = (versionId: number) => apiSend<Question>('POST', `/questions/${versionId}/checked`)
