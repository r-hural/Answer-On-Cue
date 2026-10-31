import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import { emptySubjectCoverage } from '../progress/coverage.mocks'
import type { SubjectCoverage } from '../progress/coverage'
import type { BackMatter, Chapter, DeletionPreview, Document, Job, Passage, Topic, VersionChange } from './api'

export function version(patch: Partial<Document['latest_version']> = {}): Document['latest_version'] {
  return {
    id: 10,
    version: 1,
    filename: 'ateny.txt',
    status: 'imported',
    message: null,
    char_count: 4200,
    page_count: null,
    passage_count: 3,
    ...patch,
  }
}

export function doc(patch: Partial<Document> = {}): Document {
  return { id: 1, title: 'Ateny', latest_version: version(), version_count: 1, ...patch }
}

export function passages(count = 3): Passage[] {
  return Array.from({ length: count }, (_, i) => ({
    id: 100 + i,
    ordinal: i,
    heading: null,
    locator: `sekcja ${i + 1}`,
    text: `Treść fragmentu ${i + 1}.`,
    excluded: false,
  }))
}

export function topic(patch: Partial<Topic> = {}): Topic {
  return {
    id: 5,
    title: 'Ustrój',
    description: null,
    origin: 'ai',
    status: 'active',
    objectives: [
      { id: 50, text: 'Opisać ustrój' },
      { id: 51, text: 'Wyjaśnić rolę rady' },
    ],
    passage_count: 3,
    question_count: 2,
    ...patch,
  }
}

export function job(patch: Partial<Job> = {}): Job {
  return {
    id: 7,
    kind: 'topics.extract',
    status: 'running',
    steps_total: 4,
    steps_done: 1,
    error_message: null,
    subject_id: 1,
    document_version_id: 10,
    resumable: false,
    usage: [],
    subject_name: 'Biologia komórki',
    document_title: 'Błony biologiczne',
    ...patch,
  }
}

export interface World {
  documents: Document[]
  topics: Topic[]
  jobs: Job[]
  hasKey: boolean
  passages: Passage[]
  backMatter: BackMatter
  questionCount: number
  /** Successive answers of GET /api/jobs/:id; the last one repeats. */
  jobPolls: Job[]
  requests: { method: string; path: string; body: unknown; search?: string }[]
  uploadStatus: number
  /** When set, PATCH /api/topics/:id answers 422 with this message. */
  topicError: string | null
  /** The coverage of the subject page's Coverage box; none means a subject without topics. */
  coverage?: SubjectCoverage
  /** What GET /api/subjects/1/deletion-preview shows, and what the DELETE answers. */
  deletion: DeletionPreview
  /** When set, the next DELETE answers 409 with this as the changed summary. */
  deletionChanged: DeletionPreview | null
  /** The subject's chapters (chapteredWorld only). */
  chapters?: Chapter[]
  /** When set, POST /api/documents/:id/chapter answers 409 with this message. */
  moveError?: string | null
  /** What a move answers as topics_moved. */
  topicsMoved?: number[]
}

export function deletionPreview(patch: Partial<DeletionPreview> = {}): DeletionPreview {
  return {
    subject: 'Historia',
    documents: 2,
    versions: 3,
    passages: 40,
    files: 3,
    bytes: 1000,
    topics: 4,
    objectives: 9,
    questions: 12,
    question_versions: 15,
    rounds: 3,
    answers: 20,
    recordings: 18,
    recording_bytes: 2.1 * 1024 ** 2,
    fingerprint: 'fp-1',
    ...patch,
  }
}

export function setupWorld(patch: Partial<World> = {}): World {
  const world: World = {
    documents: [],
    topics: [],
    jobs: [],
    hasKey: true,
    passages: passages(),
    backMatter: { passage_ids: [], count: 0, first_locator: null, last_locator: null, already_excluded: false },
    questionCount: 0,
    jobPolls: [],
    requests: [],
    uploadStatus: 201,
    topicError: null,
    deletion: deletionPreview(),
    deletionChanged: null,
    ...patch,
  }
  const log = async (request: Request) => {
    const text = request.method === 'GET' ? '' : await request.clone().text()
    let body: unknown = text
    try {
      body = text ? JSON.parse(text) : undefined
    } catch {
      // multipart bodies stay text
    }
    const url = new URL(request.url)
    world.requests.push({ method: request.method, path: url.pathname, body, search: url.search })
  }
  server.use(
    http.get('/api/subjects/1', () =>
      HttpResponse.json({ id: 1, name: 'Historia', description: null, document_count: world.documents.length, language: 'pl' }),
    ),
    http.get('/api/subjects/1/documents', () => HttpResponse.json(world.documents)),
    http.get('/api/coverage/subjects/:id', ({ params }) =>
      HttpResponse.json(world.coverage ?? emptySubjectCoverage(Number(params.id))),
    ),
    http.get('/api/providers/status', () =>
      HttpResponse.json({
        keys: [{ provider: 'openai', configured: world.hasKey, masked: world.hasKey ? '••••abcd' : null, region: null }],
        roles: { generation: { provider: 'openai', model: 'm' } },
      }),
    ),
    http.get('/api/topics', () => HttpResponse.json(world.topics)),
    http.get('/api/topics/terms', () =>
      HttpResponse.json(world.topics.length ? [{ term: 'Eklezja', definition: null, topic_id: world.topics[0].id }] : []),
    ),
    http.patch('/api/topics/:id', async ({ request, params }) => {
      await log(request)
      const body = (await request.clone().json()) as { title: string }
      if (world.topicError) return HttpResponse.json({ detail: world.topicError }, { status: 422 })
      const found = world.topics.find((t) => t.id === Number(params.id))!
      found.title = body.title
      return HttpResponse.json(found)
    }),
    http.post('/api/topics/merge', async ({ request }) => {
      await log(request)
      const body = (await request.clone().json()) as { topic_ids: number[]; title: string }
      const group = world.topics.filter((t) => body.topic_ids.includes(t.id))
      const merged = topic({
        id: 90,
        title: body.title,
        origin: 'user',
        objectives: group.flatMap((t) => t.objectives),
        question_count: group.reduce((sum, t) => sum + t.question_count, 0),
      })
      world.topics = [merged, ...world.topics.filter((t) => !body.topic_ids.includes(t.id))]
      return HttpResponse.json(merged)
    }),
    http.post('/api/topics/:id/split', async ({ request, params }) => {
      await log(request)
      const body = (await request.clone().json()) as {
        parts: { title: string; objective_ids: number[] }[]
      }
      const original = world.topics.find((t) => t.id === Number(params.id))!
      const parts = body.parts.map((part, n) =>
        topic({
          id: 100 + n,
          title: part.title,
          origin: 'user',
          objectives: original.objectives.filter((o) => part.objective_ids.includes(o.id)),
          question_count: 0,
        }),
      )
      world.topics = [...parts, ...world.topics.filter((t) => t.id !== original.id)]
      return HttpResponse.json(parts)
    }),
    http.delete('/api/topics/:id', async ({ request, params }) => {
      await log(request)
      const found = world.topics.find((t) => t.id === Number(params.id))!
      world.topics = world.topics.filter((t) => t.id !== found.id)
      return HttpResponse.json({ topic_id: found.id, questions_retired: found.question_count })
    }),
    http.get('/api/questions', () => HttpResponse.json(Array.from({ length: world.questionCount }, () => ({})))),
    http.get('/api/jobs', () => HttpResponse.json(world.jobs)),
    http.get('/api/jobs/:id', () => {
      const next = world.jobPolls.length > 1 ? world.jobPolls.shift()! : world.jobPolls[0]
      return HttpResponse.json(next ?? world.jobs[0])
    }),
    http.post('/api/jobs/:id/resume', async ({ request }) => {
      await log(request)
      return HttpResponse.json(job({ status: 'queued', steps_done: 2 }), { status: 202 })
    }),
    http.post('/api/topics/extract', async ({ request }) => {
      await log(request)
      return HttpResponse.json({ job_id: 7 }, { status: 202 })
    }),
    http.post('/api/questions/generate', async ({ request }) => {
      await log(request)
      return HttpResponse.json({ job_id: 8 }, { status: 202 })
    }),
    http.post('/api/subjects/1/documents', async ({ request }) => {
      await log(request)
      if (world.uploadStatus !== 201) {
        return HttpResponse.json({ detail: 'The file is empty.' }, { status: world.uploadStatus })
      }
      const created = doc({ id: 2, title: 'nowy', latest_version: version({ id: 20, filename: 'nowy.txt' }) })
      world.documents = [...world.documents, created]
      return HttpResponse.json(created, { status: 201 })
    }),
    http.get('/api/documents/:id', ({ params }) => {
      const found = world.documents.find((d) => d.id === Number(params.id)) ?? doc({ id: Number(params.id) })
      return HttpResponse.json({ ...found, versions: [found.latest_version] })
    }),
    http.get('/api/document-versions/:id/preview', () =>
      HttpResponse.json({ status: 'imported', message: null, char_count: 4200, page_count: null, text: 'A'.repeat(3500) }),
    ),
    http.get('/api/document-versions/:id/passages', () =>
      HttpResponse.json({ total: world.passages.length, items: world.passages }),
    ),
    http.get('/api/document-versions/:id/back-matter', () => HttpResponse.json(world.backMatter)),
    http.post('/api/document-versions/:id/passages/exclusion', async ({ request }) => {
      await log(request)
      const body = (await request.clone().json()) as { passage_ids: number[]; excluded: boolean }
      world.passages = world.passages.map((p) =>
        body.passage_ids.includes(p.id) ? { ...p, excluded: body.excluded } : p,
      )
      return HttpResponse.json({ updated: body.passage_ids.length })
    }),
    http.patch('/api/passages/:id', async ({ request, params }) => {
      await log(request)
      const body = (await request.clone().json()) as { excluded: boolean }
      const found = world.passages.find((p) => p.id === Number(params.id))!
      found.excluded = body.excluded
      return HttpResponse.json(found)
    }),
    http.get('/api/subjects/1/deletion-preview', () => HttpResponse.json(world.deletion)),
    http.delete('/api/subjects/1', async ({ request }) => {
      await log(request)
      if (world.deletionChanged) {
        const changed = world.deletionChanged
        world.deletionChanged = null
        world.deletion = changed
        return HttpResponse.json(
          { detail: { message: 'This subject changed since you looked. Check the new summary.', preview: changed } },
          { status: 409 },
        )
      }
      return HttpResponse.json(world.deletion)
    }),
    http.post('/api/documents/:id/chapter', async ({ request, params }) => {
      await log(request)
      if (world.moveError) return HttpResponse.json({ detail: world.moveError }, { status: 409 })
      const body = (await request.clone().json()) as { chapter_id?: number }
      return HttpResponse.json({
        document_id: Number(params.id),
        chapter_id: body.chapter_id ?? 99,
        topics_moved: world.topicsMoved ?? [],
      })
    }),
  )
  return world
}

export function chapter(patch: Partial<Chapter> = {}): Chapter {
  return {
    id: 1,
    subject_id: 1,
    name: null,
    is_default: true,
    ordinal: 0,
    document_count: 1,
    topic_count: 2,
    question_count: 3,
    ...patch,
  }
}

const emptyChapter = { is_default: false, document_count: 0, topic_count: 0, question_count: 0 }

/**
 * Opt-in: a subject with two chapters. The unnamed first chapter (1) holds „Ateny” (topics 5 and 6,
 * with questions); „Rewolucja francuska” (2) holds „Rewolucja” (topic 7, extracted, no questions yet).
 * GET /api/topics and /api/questions follow `chapter_id`; the chapter endpoints change `world.chapters`.
 */
export function chapteredWorld(patch: Partial<World> = {}): World {
  const world = setupWorld({
    documents: [
      doc({
        id: 1,
        title: 'Ateny',
        chapter_id: 1,
        latest_version: version({
          id: 10,
          topics_extracted_at: '2026-10-20T10:00:00',
          questions_generated_at: '2026-10-20T11:00:00',
          topic_count: 2,
        }),
      }),
      doc({
        id: 3,
        title: 'Rewolucja',
        chapter_id: 2,
        latest_version: version({
          id: 30,
          filename: 'rewolucja.txt',
          topics_extracted_at: '2026-10-24T10:00:00',
          questions_generated_at: null,
          topic_count: 1,
        }),
      }),
    ],
    topics: [
      topic({ id: 5, title: 'Ustrój', chapter_id: 1, question_counts: { materials: 2 } }),
      topic({ id: 6, title: 'Wojny perskie', chapter_id: 1, question_count: 1, question_counts: { materials: 1 } }),
      topic({ id: 7, title: 'Terror', chapter_id: 2, question_count: 0, question_counts: {} }),
    ],
    chapters: [
      chapter(),
      chapter({ id: 2, name: 'Rewolucja francuska', is_default: false, ordinal: 1, topic_count: 1, question_count: 0 }),
    ],
    questionCount: 3,
    ...patch,
  })
  const chapters = () => world.chapters ?? []
  const log = async (request: Request) => {
    const text = await request.clone().text()
    const url = new URL(request.url)
    world.requests.push({
      method: request.method,
      path: url.pathname,
      body: text ? JSON.parse(text) : undefined,
      search: url.search,
    })
  }
  const chapterOf = (request: Request) => {
    const value = new URL(request.url).searchParams.get('chapter_id')
    return value === null ? null : Number(value)
  }
  const addChapter = (name: string | null) => {
    const created = chapter({ ...emptyChapter, id: 3, name, ordinal: chapters().length })
    world.chapters = [...chapters(), created]
    return created
  }
  server.use(
    http.get('/api/subjects/1', () =>
      HttpResponse.json({
        id: 1,
        name: 'Historia',
        description: null,
        document_count: world.documents.length,
        chapter_count: chapters().length,
        language: 'pl',
      }),
    ),
    http.get('/api/subjects/1/chapters', () => HttpResponse.json(chapters())),
    http.post('/api/subjects/1/chapters', async ({ request }) => {
      await log(request)
      const body = (await request.clone().json()) as { name: string }
      return HttpResponse.json(addChapter(body.name), { status: 201 })
    }),
    http.patch('/api/chapters/:id', async ({ request, params }) => {
      await log(request)
      const body = (await request.clone().json()) as { name: string }
      const found = chapters().find((c) => c.id === Number(params.id))!
      found.name = body.name
      return HttpResponse.json(found)
    }),
    http.delete('/api/chapters/:id', async ({ request, params }) => {
      await log(request)
      world.chapters = chapters().filter((c) => c.id !== Number(params.id))
      return new HttpResponse(null, { status: 204 })
    }),
    http.get('/api/topics', ({ request }) => {
      const id = chapterOf(request)
      return HttpResponse.json(id === null ? world.topics : world.topics.filter((t) => t.chapter_id === id))
    }),
    http.get('/api/questions', ({ request }) => {
      const id = chapterOf(request)
      const count = id === null ? world.questionCount : (chapters().find((c) => c.id === id)?.question_count ?? 0)
      return HttpResponse.json(Array.from({ length: count }, () => ({})))
    }),
    http.post('/api/documents/:id/chapter', async ({ request, params }) => {
      await log(request)
      if (world.moveError) return HttpResponse.json({ detail: world.moveError }, { status: 409 })
      const body = (await request.clone().json()) as { chapter_id?: number; new_chapter_name?: string }
      const target = body.chapter_id ?? addChapter(body.new_chapter_name ?? null).id
      world.documents = world.documents.map((d) => (d.id === Number(params.id) ? { ...d, chapter_id: target } : d))
      return HttpResponse.json({ document_id: Number(params.id), chapter_id: target, topics_moved: world.topicsMoved ?? [] })
    }),
  )
  return world
}

export function versionChange(patch: Partial<VersionChange> = {}): VersionChange {
  return {
    token: 'a'.repeat(32),
    document_id: 1,
    filename: 'Demokracja_poprawione.pdf',
    current_version: 2,
    new_version: 3,
    status: 'imported',
    message: null,
    page_count: 146,
    passage_count: 839,
    unchanged: 790,
    changed: 14,
    removed: 8,
    new: 27,
    exclusions_carried: 41,
    exclusions_total: 41,
    questions_to_review: 5,
    topics_without_material: 0,
    fingerprint: 'fp-version',
    ...patch,
  }
}

export interface VersionsWorld {
  requests: { method: string; path: string; body: unknown }[]
  /** What the preview answers: a change, or an error status with its message. */
  preview: VersionChange | { status: number; detail: string }
  /** When set, the first apply answers 409 with this summary. */
  changed: VersionChange | null
}

/**
 * Opt-in: the replace flow of document 1 („Demokracja_atenska.pdf”, version 2 of 2, 142 pages, 812
 * passages) of subject 1 („Demokracja ateńska”).
 */
export function versionsWorld(patch: Partial<VersionsWorld> = {}): VersionsWorld {
  const world: VersionsWorld = { requests: [], preview: versionChange(), changed: null, ...patch }
  const log = async (request: Request) => {
    let body: unknown
    try {
      body = await request.clone().json()
    } catch {
      body = undefined
    }
    world.requests.push({ method: request.method, path: new URL(request.url).pathname, body })
  }
  const latest = version({
    id: 20,
    version: 2,
    filename: 'Demokracja_atenska.pdf',
    page_count: 142,
    passage_count: 812,
    created_at: '2026-10-20T10:00:00',
  })
  server.use(
    http.get('/api/subjects/1', () =>
      HttpResponse.json({ id: 1, name: 'Demokracja ateńska', description: null, document_count: 1, language: 'pl' }),
    ),
    http.get('/api/documents/1', () =>
      HttpResponse.json({
        ...doc({ id: 1, title: 'Demokracja ateńska', latest_version: latest, version_count: 2 }),
        versions: [version({ id: 10, version: 1, created_at: '2026-10-19T10:00:00' }), latest],
      }),
    ),
    http.post('/api/documents/1/versions/preview', async ({ request }) => {
      await log(request)
      const answer = world.preview
      if ('detail' in answer) return HttpResponse.json({ detail: answer.detail }, { status: answer.status })
      return HttpResponse.json(answer)
    }),
    http.post('/api/documents/1/versions/:token/apply', async ({ request }) => {
      await log(request)
      if (world.changed) {
        const changed = world.changed
        world.changed = null
        return HttpResponse.json(
          {
            detail: {
              message: 'The document or its questions changed since you looked. Check the new summary.',
              preview: changed,
            },
          },
          { status: 409 },
        )
      }
      return HttpResponse.json(
        { document: doc({ id: 1, version_count: 3 }), questions_to_review: 5, topics_relinked: 2 },
        { status: 201 },
      )
    }),
    http.delete('/api/documents/1/versions/:token', async ({ request }) => {
      await log(request)
      return new HttpResponse(null, { status: 204 })
    }),
  )
  return world
}

// Server-free, so src/test/msw.ts can take languageHandlers as default handlers.
export { LANGUAGES, languageHandlers, languagesWith } from './languages.mocks'
