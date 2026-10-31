import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import type { Document, Job, Subject, Term, Topic } from '../library/api'
import { doc, job, topic, version } from '../library/library.mocks'
import type { Preferences } from '../settings/api'

export const preferences: Preferences = {
  transcription_model: 'gpt-transcribe',
  generation_provider: 'openai',
  generation_model: 'gpt-6.1-sol',
  assessment_provider: 'openai',
  assessment_model: 'gpt-6.1-sol',
  voice_provider: 'piper',
  voice_name: 'pl_PL-gosia-medium',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
  round_question_count: 10,
  round_prep_seconds: 30,
  round_answer_seconds: 120,
}

export interface Seen {
  method: string
  path: string
  body: unknown
}

export interface QuickWorld {
  subject: Subject
  documents: Document[]
  topics: Topic[]
  terms: Term[]
  jobs: Job[]
  /** Successive answers of GET /api/jobs/:id; the last one repeats. */
  jobPolls: Job[]
  activeQuestions: number
  preferences: Preferences
  /** Subject names that answer 409. */
  takenNames: string[]
  uploadStatus: number
  roundStatus: number
  requests: Seen[]
}

export function topics(): Topic[] {
  return [
    topic({ id: 11, title: 'Budowa błony', question_count: 0 }),
    topic({ id: 12, title: 'Transport przez błonę', question_count: 0 }),
    topic({ id: 13, title: 'Mitochondria', question_count: 0 }),
  ]
}

export function setupQuick(patch: Partial<QuickWorld> = {}): QuickWorld {
  const world: QuickWorld = {
    subject: { id: 5, name: 'Biologia', description: null, document_count: 1 },
    documents: [doc({ latest_version: version({ filename: 'biologia.pdf', page_count: 24, char_count: 31200, passage_count: 96 }) })],
    topics: [],
    terms: [],
    jobs: [],
    jobPolls: [],
    activeQuestions: 0,
    preferences,
    takenNames: [],
    uploadStatus: 201,
    roundStatus: 201,
    requests: [],
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
    world.requests.push({ method: request.method, path: new URL(request.url).pathname, body })
    return body
  }
  server.use(
    http.post('/api/subjects', async ({ request }) => {
      const body = (await log(request)) as { name: string }
      if (world.takenNames.includes(body.name))
        return HttpResponse.json({ detail: 'A subject with this name already exists.' }, { status: 409 })
      world.subject = { ...world.subject, name: body.name }
      return HttpResponse.json(world.subject, { status: 201 })
    }),
    http.get('/api/subjects/5', () => HttpResponse.json(world.subject)),
    http.patch('/api/subjects/5', async ({ request }) => {
      const body = (await log(request)) as { name: string }
      world.subject = { ...world.subject, name: body.name }
      return HttpResponse.json(world.subject)
    }),
    http.delete('/api/subjects/5', async ({ request }) => {
      await log(request)
      return HttpResponse.json({ subject: world.subject.name, documents: 0, versions: 0, passages: 0, files: 0, bytes: 0 })
    }),
    http.post('/api/subjects/5/documents', async ({ request }) => {
      await log(request)
      if (world.uploadStatus !== 201)
        return HttpResponse.json({ detail: 'Unsupported file type.' }, { status: world.uploadStatus })
      return HttpResponse.json(world.documents[0], { status: 201 })
    }),
    http.get('/api/subjects/5/documents', () => HttpResponse.json(world.documents)),
    http.get('/api/topics', () => HttpResponse.json(world.topics)),
    http.get('/api/topics/terms', () => HttpResponse.json(world.terms)),
    http.get('/api/questions', () =>
      HttpResponse.json(Array.from({ length: world.activeQuestions }, () => ({}))),
    ),
    http.get('/api/settings', () => HttpResponse.json(world.preferences)),
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
      world.jobPolls = world.jobPolls.length ? world.jobPolls : [job({ id: 7, status: 'running' })]
      return HttpResponse.json({ job_id: 7 }, { status: 202 })
    }),
    http.post('/api/questions/generate', async ({ request }) => {
      await log(request)
      return HttpResponse.json({ job_id: 8 }, { status: 202 })
    }),
    http.post('/api/rounds', async ({ request }) => {
      await log(request)
      if (world.roundStatus !== 201)
        return HttpResponse.json({ detail: 'Not enough questions for this round.' }, { status: world.roundStatus })
      return HttpResponse.json({ id: 77 }, { status: 201 })
    }),
  )
  return world
}

export const requestsTo = (world: QuickWorld, method: string, path: RegExp) =>
  world.requests.filter((r) => r.method === method && path.test(r.path))
