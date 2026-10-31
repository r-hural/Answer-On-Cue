import { apiGet, apiSend, apiUpload } from '../../api/client'

export interface Subject {
  id: number
  name: string
  description: string | null
  document_count: number
  /** Missing on an older server: one chapter. */
  chapter_count?: number
  /** Questions in review because a document was replaced; missing on an older server: none. */
  source_review_count?: number
  /** The content language code; missing on an older server: Polish. */
  language?: string
}

/** A content language: a subject uses one, fixed when the subject is created. */
export interface ContentLanguage {
  code: string
  bcp47: string
  name_en: string
  name_native: string
  quotes: [string, string]
  enabled: boolean
  voice_provider: string | null
  voice_name: string | null
  subject_count: number
}

export interface DeletionPreview {
  subject: string
  documents: number
  versions: number
  passages: number
  files: number
  bytes: number
  topics: number
  objectives: number
  questions: number
  question_versions: number
  rounds: number
  answers: number
  recordings: number
  recording_bytes: number
  fingerprint: string
}

export interface Version {
  id: number
  version: number
  filename: string
  status: string
  message: string | null
  char_count: number
  page_count: number | null
  passage_count: number
  /** When the newest finished topic extraction of this version ended; null or missing: never or unknown. */
  topics_extracted_at?: string | null
  /** The same for question generation for this version. */
  questions_generated_at?: string | null
  /** Active topics with material from this version. */
  topic_count?: number
  created_at?: string
  /** Answered attempts and rounds that cite this version (the document detail only). */
  answer_count?: number
  round_count?: number
}

export interface Document {
  id: number
  title: string
  latest_version: Version
  version_count: number
  chapter_id?: number | null
}

export interface Passage {
  id: number
  ordinal: number
  heading: string | null
  locator: string
  text: string
  excluded: boolean
}

export interface PassagePage {
  total: number
  items: Passage[]
}

export interface Preview {
  status: string
  message: string | null
  char_count: number
  page_count: number | null
  text: string
}

export interface BackMatter {
  passage_ids: number[]
  count: number
  first_locator: string | null
  last_locator: string | null
  already_excluded: boolean
}

export interface Objective {
  id: number
  text: string
}

export interface Topic {
  id: number
  title: string
  description: string | null
  origin: string
  status: string
  objectives: Objective[]
  passage_count: number
  question_count: number
  children?: Topic[]
  /** Active and needs-review questions per source policy. */
  question_counts?: Partial<Record<'materials' | 'expanded' | 'outside', number>>
  chapter_id?: number | null
}

export interface SplitPart {
  title: string
  objective_ids: number[]
  passage_ids: number[]
}

export interface TopicRemoved {
  topic_id: number
  questions_retired: number
}

export interface Term {
  term: string
  definition: string | null
  topic_id: number | null
}

export interface JobUsage {
  provider: string
  model: string
  calls: number
  errors: number
  input_tokens: number
  output_tokens: number
}

export interface Job {
  id: number
  kind: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'partial'
  steps_total: number
  steps_done: number
  error_message: string | null
  subject_id: number
  document_version_id: number | null
  resumable: boolean
  usage: JobUsage[]
  subject_name: string
  document_title: string | null
  /** The chapter the job worked in, when known. */
  chapter_id?: number | null
}

export const EXTRACT_KIND = 'topics.extract'
export const GENERATE_KIND = 'questions.generate'

export const PASSAGE_PAGE = 50

export const listSubjects = () => apiGet<Subject[]>('/subjects')
export const getSubject = (id: number) => apiGet<Subject>(`/subjects/${id}`)
export const createSubject = (name: string, description: string, language: string) =>
  apiSend<Subject>('POST', '/subjects', { name, description: description || null, language })
export const listLanguages = () => apiGet<ContentLanguage[]>('/languages')
export const deletionPreview = (id: number) =>
  apiGet<DeletionPreview>(`/subjects/${id}/deletion-preview`)
/** The fingerprint ties the deletion to the summary that was shown; without it, it deletes as is. */
export const deleteSubject = (id: number, fingerprint?: string) =>
  apiSend<DeletionPreview>(
    'DELETE',
    `/subjects/${id}?confirm=true${fingerprint === undefined ? '' : `&fingerprint=${encodeURIComponent(fingerprint)}`}`,
  )

export const listDocuments = (subjectId: number) =>
  apiGet<Document[]>(`/subjects/${subjectId}/documents`)
/** Without a chapter the document goes to the subject's first chapter; a new name creates one. */
export const uploadDocument = (subjectId: number, file: File, chapter?: { id?: number; name?: string }) => {
  const form = new FormData()
  form.append('file', file)
  if (chapter?.id !== undefined) form.append('chapter_id', String(chapter.id))
  if (chapter?.name !== undefined) form.append('chapter_name', chapter.name)
  return apiUpload<Document>(`/subjects/${subjectId}/documents`, form)
}

export const getPreview = (versionId: number) =>
  apiGet<Preview>(`/document-versions/${versionId}/preview`)
export const listPassages = (versionId: number, offset: number) =>
  apiGet<PassagePage>(
    `/document-versions/${versionId}/passages?offset=${offset}&limit=${PASSAGE_PAGE}`,
  )
export const setPassageExcluded = (id: number, excluded: boolean) =>
  apiSend<Passage>('PATCH', `/passages/${id}`, { excluded })
export const getBackMatter = (versionId: number) =>
  apiGet<BackMatter>(`/document-versions/${versionId}/back-matter`)
export const setExclusion = (versionId: number, passageIds: number[], excluded: boolean) =>
  apiSend<{ updated: number }>('POST', `/document-versions/${versionId}/passages/exclusion`, {
    passage_ids: passageIds,
    excluded,
  })

const chapterParam = (chapterId?: number | null) => (chapterId == null ? '' : `&chapter_id=${chapterId}`)

export const listTopics = (subjectId: number, chapterId?: number | null) =>
  apiGet<Topic[]>(`/topics?subject_id=${subjectId}${chapterParam(chapterId)}`)
/** The topic tree as one list: every topic followed by its children. */
export function flattenTopics(topics: Topic[]): Topic[] {
  return topics.flatMap((topic) => [topic, ...flattenTopics(topic.children ?? [])])
}
export const renameTopic = (id: number, title: string) =>
  apiSend<Topic>('PATCH', `/topics/${id}`, { title })
export const mergeTopics = (topicIds: number[], title: string) =>
  apiSend<Topic>('POST', '/topics/merge', { topic_ids: topicIds, title })
export const splitTopic = (id: number, parts: SplitPart[]) =>
  apiSend<Topic[]>('POST', `/topics/${id}/split`, { parts })
export const removeTopic = (id: number) => apiSend<TopicRemoved>('DELETE', `/topics/${id}`)
export const renameSubject = (id: number, name: string) =>
  apiSend<Subject>('PATCH', `/subjects/${id}`, { name })
export const listTerms = (subjectId: number) =>
  apiGet<Term[]>(`/topics/terms?subject_id=${subjectId}`)
export const startExtraction = (versionId: number) =>
  apiSend<{ job_id: number }>('POST', '/topics/extract', { document_version_id: versionId })

export interface GenerateOptions {
  per_topic: number
  style: string
  difficulty: string
  source_policy?: 'materials' | 'expanded' | 'outside'
  topic_ids?: number[]
  chapter_id?: number
  document_version_id?: number
}
export const startGeneration = (subjectId: number, options: GenerateOptions) =>
  apiSend<{ job_id: number }>('POST', '/questions/generate', { subject_id: subjectId, ...options })
export const countQuestions = async (subjectId: number, chapterId?: number | null) =>
  (
    await apiGet<unknown[]>(
      `/questions?subject_id=${subjectId}&status=active&status=needs_review${chapterParam(chapterId)}`,
    )
  ).length

export const listJobs = (subjectId: number) => apiGet<Job[]>(`/jobs?subject_id=${subjectId}&limit=5`)
export const getJob = (id: number) => apiGet<Job>(`/jobs/${id}`)
/** Queued and running tasks of every subject. */
export const listLiveJobs = () => apiGet<Job[]>('/jobs?live=true')
export const resumeJob = (id: number) => apiSend<Job>('POST', `/jobs/${id}/resume`)

export interface Chapter {
  id: number
  subject_id: number
  /** null only for the first chapter while it is unnamed. */
  name: string | null
  is_default: boolean
  ordinal: number
  document_count: number
  topic_count: number
  question_count: number
}

export interface MoveResult {
  document_id: number
  chapter_id: number
  /** Topics now in the target chapter. */
  topics_moved: number[]
}

export const listChapters = (subjectId: number) => apiGet<Chapter[]>(`/subjects/${subjectId}/chapters`)
export const createChapter = (subjectId: number, name: string) =>
  apiSend<Chapter>('POST', `/subjects/${subjectId}/chapters`, { name })
export const renameChapter = (id: number, name: string) => apiSend<Chapter>('PATCH', `/chapters/${id}`, { name })
export const deleteChapter = (id: number) => apiSend<void>('DELETE', `/chapters/${id}`)
/** Exactly one of the two targets. */
export const moveDocument = (documentId: number, target: { chapter_id: number } | { new_chapter_name: string }) =>
  apiSend<MoveResult>('POST', `/documents/${documentId}/chapter`, target)

export interface DocumentDetail extends Document {
  versions: Version[]
}

/** What replacing a document with the staged file would change; nothing is stored yet. */
export interface VersionChange {
  token: string
  document_id: number
  filename: string
  current_version: number
  new_version: number
  status: string
  message: string | null
  page_count: number | null
  passage_count: number
  unchanged: number
  changed: number
  removed: number
  new: number
  exclusions_carried: number
  exclusions_total: number
  questions_to_review: number
  topics_without_material: number
  fingerprint: string
}

export interface VersionApplied {
  document: Document
  questions_to_review: number
  topics_relinked: number
}

export const getDocument = (documentId: number) => apiGet<DocumentDetail>(`/documents/${documentId}`)
export const previewVersion = (documentId: number, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiUpload<VersionChange>(`/documents/${documentId}/versions/preview`, form)
}
export const applyVersion = (documentId: number, token: string, fingerprint: string) =>
  apiSend<VersionApplied>('POST', `/documents/${documentId}/versions/${token}/apply`, { fingerprint })
export const discardVersion = (documentId: number, token: string) =>
  apiSend<void>('DELETE', `/documents/${documentId}/versions/${token}`)
