import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw'
import type {
  CoverageStatus,
  HistoryEntry,
  ObjectiveCoverage,
  SubjectCoverage,
  TopicCoverage,
  TopicDetail,
  TopicSource,
} from './coverage'

const AT = '2026-10-22T12:00:00Z'

export function objectiveCoverage(patch: Partial<ObjectiveCoverage> = {}): ObjectiveCoverage {
  return {
    objective_id: 1,
    text: 'Opisać procedurę ostracyzmu',
    status: 'needs_work',
    assisted: false,
    answers: 3,
    independent_answers: 3,
    last_practised_at: AT,
    recent_factual: [51, 44, 60],
    deciding: { attempt_id: 11, round_id: 5, at: AT, band: 'needs_improvement', factual: 51, independent: true },
    median_preparation_used_ms: 8000,
    question_count: 2,
    ...patch,
  }
}

export function notPractised(patch: Partial<ObjectiveCoverage> = {}): ObjectiveCoverage {
  return objectiveCoverage({
    status: 'not_practised',
    answers: 0,
    independent_answers: 0,
    last_practised_at: null,
    recent_factual: [],
    deciding: null,
    median_preparation_used_ms: null,
    ...patch,
  })
}

export function topicCoverage(patch: Partial<TopicCoverage> = {}): TopicCoverage {
  return {
    topic_id: 7,
    title: 'Ostracyzm: procedura i cel',
    status: 'needs_work',
    objectives: [
      objectiveCoverage(),
      objectiveCoverage({
        objective_id: 2,
        text: 'Wyjaśnić cel polityczny ostracyzmu',
        status: 'improving',
        assisted: true,
        answers: 2,
        independent_answers: 1,
        recent_factual: [78],
        deciding: { attempt_id: 12, round_id: 6, at: AT, band: 'good', factual: 78, independent: false },
        median_preparation_used_ms: null,
      }),
    ],
    ...patch,
  }
}

const STATUS_KEYS: CoverageStatus[] = ['strong', 'improving', 'needs_work', 'not_practised']

function counts(statuses: CoverageStatus[]): Record<CoverageStatus, number> {
  return Object.fromEntries(STATUS_KEYS.map((key) => [key, statuses.filter((s) => s === key).length])) as Record<
    CoverageStatus,
    number
  >
}

/** The counts follow the topics unless a patch states them. */
export function subjectCoverage(patch: Partial<SubjectCoverage> = {}): SubjectCoverage {
  const topics = patch.topics ?? [
    topicCoverage(),
    topicCoverage({
      topic_id: 8,
      title: 'Reformy Solona',
      status: 'improving',
      objectives: [
        objectiveCoverage({
          objective_id: 3,
          text: 'Opisać podział na klasy majątkowe',
          status: 'strong',
          recent_factual: [88, 84],
          answers: 4,
          median_preparation_used_ms: 4000,
        }),
        notPractised({ objective_id: 4, text: 'Ocenić znaczenie reform' }),
      ],
    }),
  ]
  const lines = topics.flatMap((topic) => topic.objectives.map((line) => line.status))
  return {
    subject_id: 3,
    subject_name: 'Demokracja ateńska',
    line_total: lines.length,
    line_counts: counts(lines),
    topic_counts: counts(topics.map((topic) => topic.status)),
    ...patch,
    topics,
  }
}

export function emptySubjectCoverage(subjectId: number, name = 'Demokracja ateńska'): SubjectCoverage {
  return subjectCoverage({ subject_id: subjectId, subject_name: name, topics: [] })
}

export function historyEntry(patch: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    attempt_id: 11,
    round_id: 5,
    at: AT,
    question_text: 'Wyjaśnij, na czym polegała procedura ostracyzmu w Atenach.',
    style: 'open',
    difficulty: 'apply',
    objective_id: 1,
    attempt_kind: 'initial',
    independent: true,
    outcome: 'answered',
    status: 'ready',
    band: 'needs_improvement',
    factual: 51,
    language: 66,
    preparation_used_ms: 8200,
    ...patch,
  }
}

export function topicSource(patch: Partial<TopicSource> = {}): TopicSource {
  return {
    passage_id: 101,
    document_id: 1,
    document_title: 'Demokracja_atenska_rozdzialy_1-4.pdf',
    document_version: 1,
    locator: 's. 14 ¶1',
    heading: null,
    excerpt: 'Raz w roku zgromadzenie decydowało, czy przeprowadzić ostracyzm…',
    ...patch,
  }
}

export function topicDetail(patch: Partial<TopicDetail> = {}): TopicDetail {
  const topic = topicCoverage()
  return {
    topic_id: topic.topic_id,
    subject_id: 3,
    subject_name: 'Demokracja ateńska',
    title: topic.title,
    description: null,
    status: topic.status,
    objectives: topic.objectives,
    question_count: 6,
    terms: ['ostrakon', 'ostracyzm'],
    passage_count: 2,
    history: [
      historyEntry({
        attempt_id: 12,
        round_id: 6,
        attempt_kind: 'immediate',
        independent: false,
        band: 'good',
        factual: 78,
        language: 71,
        preparation_used_ms: 5000,
      }),
      historyEntry(),
    ],
    sources: [topicSource(), topicSource({ passage_id: 102, locator: 's. 14 ¶2', excerpt: 'Do ważności głosowania potrzeba było co najmniej 6000 głosów…' })],
    ...patch,
  }
}

export interface SubjectRef {
  id: number
  name: string
}

export interface CoverageWorld {
  subjects: SubjectRef[]
  coverage: Record<number, SubjectCoverage>
  topics: Record<number, TopicDetail>
  /** Every requested URL (path and query), in order. */
  requests: string[]
}

/** MSW handlers for the subject list and both coverage endpoints; a missing entry is a 404. */
export function setupCoverageWorld(patch: Partial<Omit<CoverageWorld, 'requests'>> = {}): CoverageWorld {
  const world: CoverageWorld = {
    subjects: [
      { id: 3, name: 'Demokracja ateńska' },
      { id: 4, name: 'Biologia komórki' },
    ],
    coverage: { 3: subjectCoverage(), 4: emptySubjectCoverage(4, 'Biologia komórki') },
    topics: { 7: topicDetail() },
    requests: [],
    ...patch,
  }
  const log = (request: Request) => {
    const url = new URL(request.url)
    world.requests.push(url.pathname + url.search)
  }
  server.use(
    http.get('/api/subjects', ({ request }) => {
      log(request)
      return HttpResponse.json(world.subjects.map((s) => ({ ...s, description: null, document_count: 1 })))
    }),
    http.get('/api/coverage/subjects/:id', ({ request, params }) => {
      log(request)
      const found = world.coverage[Number(params.id)]
      return found ? HttpResponse.json(found) : HttpResponse.json({ detail: 'Subject not found.' }, { status: 404 })
    }),
    http.get('/api/coverage/topics/:id', ({ request, params }) => {
      log(request)
      const found = world.topics[Number(params.id)]
      return found ? HttpResponse.json(found) : HttpResponse.json({ detail: 'Topic not found.' }, { status: 404 })
    }),
  )
  return world
}
