import { apiGet, apiSend } from '../../api/client'

export const countActive = async (subjectId: number) =>
  (await apiGet<unknown[]>(`/questions?subject_id=${subjectId}&status=active`)).length

/** The body of the quick generation; its cost estimate prices exactly this body. */
export const generationBody = (subjectId: number, topicIds: number[], perTopic: number) => ({
  subject_id: subjectId,
  topic_ids: topicIds,
  per_topic: perTopic,
  style: 'mixed',
  difficulty: 'mixed',
})

export const generateQuestions = (subjectId: number, topicIds: number[], perTopic: number) =>
  apiSend<{ job_id: number }>('POST', '/questions/generate', generationBody(subjectId, topicIds, perTopic))
