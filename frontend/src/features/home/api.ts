import { apiGet } from '../../api/client'

export interface RoundListItem {
  id: number
  status: string
  started_at: string
  finished_at: string | null
  total: number
  done: number
  feedback_timing: string
}

export const listRounds = (subjectId: number) =>
  apiGet<RoundListItem[]>(`/rounds?subject_id=${subjectId}`)
