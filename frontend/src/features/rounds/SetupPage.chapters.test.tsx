import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { SetupPage } from './SetupPage'
import { setupRoundWorld, type RoundWorld } from './rounds.mocks'

const PREFS = {
  transcription_model: 'gpt-transcribe',
  generation_provider: 'openai',
  generation_model: 'gen-1',
  assessment_provider: 'anthropic',
  assessment_model: 'asm-1',
  voice_provider: 'browser',
  voice_name: '',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
}

const topic = (id: number, title: string, chapter: number, count: number) => ({
  id,
  title,
  description: null,
  origin: 'extracted',
  status: 'active',
  objectives: [],
  passage_count: 3,
  question_count: count,
  children: [],
  chapter_id: chapter,
})

const question = (id: number, topicId: number) => ({
  version_id: id,
  family_id: id,
  version: 1,
  text: `Pytanie ${id}`,
  style: 'open',
  difficulty: 'recall',
  primary_topic_id: topicId,
  status: 'active',
})

let urls: string[] = []
beforeEach(() => {
  sessionStorage.clear()
  urls = []
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url)
    urls.push(`${request.method} ${url.pathname}${url.search}`)
  })
})
afterEach(() => server.events.removeAllListeners())

/** Subject 1 has two chapters: the unnamed first (topic 11, 6 questions) and „Rewolucja francuska” (topic 12, 4). */
function mockApi(chapterCount = 2): RoundWorld {
  const world = setupRoundWorld()
  const questions = [
    ...Array.from({ length: 6 }, (_, i) => question(i + 1, 11)),
    ...Array.from({ length: 4 }, (_, i) => question(i + 7, 12)),
  ]
  server.use(
    http.get('/api/subjects', () =>
      HttpResponse.json([
        { id: 1, name: 'Historia PL', description: null, document_count: 2, chapter_count: chapterCount },
        { id: 2, name: 'Biologia', description: null, document_count: 0, chapter_count: 1 },
      ]),
    ),
    http.get('/api/settings', () => HttpResponse.json(PREFS)),
    http.get('/api/voice/voices', () => HttpResponse.json([])),
    http.get('/api/subjects/1/chapters', () =>
      HttpResponse.json([
        { id: 1, subject_id: 1, name: null, is_default: true, ordinal: 0, document_count: 1, topic_count: 1, question_count: 6 },
        { id: 2, subject_id: 1, name: 'Rewolucja francuska', is_default: false, ordinal: 1, document_count: 1, topic_count: 1, question_count: 4 },
      ]),
    ),
    http.get('/api/topics', () => HttpResponse.json([topic(11, 'Ateny', 1, 6), topic(12, 'Terror', 2, 4)])),
    http.get('/api/subjects/:id/documents', () =>
      HttpResponse.json([
        { id: 21, title: 'Grecja', latest_version: { passage_count: 5 }, version_count: 1, chapter_id: 1 },
        { id: 22, title: 'Rewolucja', latest_version: { passage_count: 4 }, version_count: 1, chapter_id: 2 },
      ]),
    ),
    http.get('/api/questions', () => HttpResponse.json(questions)),
  )
  return world
}

function renderPage(path = '/practice') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/practice" element={<SetupPage />} />
        <Route path="/practice/:roundId/microphone" element={<p>Microphone page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const scopeRadios = () =>
  within(screen.getByRole('radiogroup', { name: 'Scope' }))
    .getAllByRole('radio')
    .map((radio) => radio.closest('label')!.textContent!.trim())

async function start(world: RoundWorld) {
  const button = await screen.findByRole('button', { name: 'Check microphone and start' })
  await waitFor(() => expect(button).toBeEnabled())
  await userEvent.click(button)
  expect(await screen.findByText('Microphone page')).toBeInTheDocument()
  const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
  return (post?.body as { config: { scope: unknown } }).config.scope
}

describe('SetupPage with chapters', () => {
  it('adds a Chapter select and defaults to the whole chapter', async () => {
    const world = mockApi()
    renderPage()
    const select = await screen.findByLabelText('Chapter')
    const options = within(select).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['Unnamed chapter', 'Rewolucja francuska'])
    expect(options[0]).not.toHaveAttribute('lang')
    expect(options[1]).not.toHaveAttribute('lang')
    expect(scopeRadios()).toEqual(['Whole chapter', 'Whole subject', 'Selected topics', 'Selected documents'])
    expect(screen.getByRole('radio', { name: 'Whole chapter' })).toBeChecked()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    // 6 questions in the first chapter, against the default 10
    expect(await screen.findByText('Only 6 questions match.')).toBeInTheDocument()
    expect(screen.getByText(/^The chapter „Unnamed chapter” gives 6 questions, not 10/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ask 6 questions' }))
    expect(await start(world)).toEqual({ kind: 'chapter', topic_ids: [], document_ids: [], chapter_id: 1 })
  })

  it('preselects ?chapter= and lists only that chapter’s topics and documents', async () => {
    const world = mockApi()
    renderPage('/practice?subject=1&chapter=2')
    const select = await screen.findByLabelText('Chapter')
    await waitFor(() => expect(select).toHaveValue('2'))
    await userEvent.click(screen.getByRole('radio', { name: 'Selected topics' }))
    const topics = screen.getByRole('list', { name: /topics/i })
    expect(within(topics).getAllByRole('checkbox')).toHaveLength(1)
    expect(within(topics).getByText('Terror')).toHaveAttribute('lang', 'pl-PL')
    await userEvent.click(screen.getByRole('radio', { name: 'Selected documents' }))
    const documents = screen.getByRole('list', { name: /documents/i })
    expect(within(documents).getByText('Rewolucja')).toBeInTheDocument()
    expect(within(documents).queryByText('Grecja')).toBeNull()
    await userEvent.click(within(documents).getByRole('checkbox'))
    const count = screen.getByLabelText('Main questions')
    await userEvent.clear(count)
    await userEvent.type(count, '2')
    expect(await start(world)).toEqual({ kind: 'documents', topic_ids: [], document_ids: [22], chapter_id: 2 })
  })

  it('the whole subject never sends a chapter', async () => {
    const world = mockApi()
    renderPage()
    await screen.findByLabelText('Chapter')
    await userEvent.click(screen.getByRole('radio', { name: 'Whole subject' }))
    expect(await start(world)).toEqual({ kind: 'subject', topic_ids: [], document_ids: [] })
  })

  it('a stored form from before chapters still opens', async () => {
    mockApi()
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify({ questionCount: 4, scopeKind: 'subject' }))
    renderPage()
    expect(await screen.findByLabelText('Chapter')).toBeInTheDocument()
    expect(screen.getByLabelText('Main questions')).toHaveValue(4)
  })
})

describe('SetupPage with one chapter', () => {
  it('shows no Chapter select, asks for no chapters and posts the subject scope as before', async () => {
    const world = mockApi(1)
    renderPage()
    expect(await screen.findByLabelText('Subject')).toHaveValue('1')
    await waitFor(() => expect(urls.some((u) => u.startsWith('GET /api/questions'))).toBe(true))
    expect(screen.queryByLabelText('Chapter')).toBeNull()
    expect(scopeRadios()).toEqual(['Whole subject', 'Selected topics', 'Selected documents'])
    expect(urls.filter((u) => u.includes('chapter'))).toEqual([])
    expect(await start(world)).toEqual({ kind: 'subject', topic_ids: [], document_ids: [] })
  })
})

describe('SetupPage chapter links and stored choices', () => {
  it('?chapter= of another chapter drops the stored topics of the old one', async () => {
    const world = mockApi()
    sessionStorage.setItem(
      'aoc.practiceForm',
      JSON.stringify({ subjectId: 1, chapterId: 1, scopeKind: 'topics', topicIds: [11], questionCount: 2 }),
    )
    renderPage('/practice?subject=1&chapter=2')
    const select = await screen.findByLabelText('Chapter')
    await waitFor(() => expect(select).toHaveValue('2'))
    expect(await screen.findByText('Select at least one topic.')).toBeInTheDocument()
    // the old chapter's choice is cleared from the form itself, not only hidden
    await waitFor(() =>
      expect(JSON.parse(sessionStorage.getItem('aoc.practiceForm')!)).toMatchObject({ chapterId: 2, topicIds: [] }),
    )
    const topics = screen.getByRole('list', { name: /topics/i })
    await waitFor(() => expect(within(topics).getByRole('checkbox')).not.toBeChecked())
    await userEvent.click(within(topics).getByRole('checkbox'))
    expect(await start(world)).toEqual({ kind: 'topics', topic_ids: [12], document_ids: [], chapter_id: 2 })
  })

  it('counts and sends only the shown chapter’s topics, never hidden stored ones', async () => {
    const world = mockApi()
    sessionStorage.setItem(
      'aoc.practiceForm',
      JSON.stringify({ subjectId: 1, chapterId: 2, scopeKind: 'topics', topicIds: [11, 12], questionCount: 2 }),
    )
    renderPage('/practice?subject=1&chapter=2')
    const select = await screen.findByLabelText('Chapter')
    await waitFor(() => expect(select).toHaveValue('2'))
    expect(await start(world)).toEqual({ kind: 'topics', topic_ids: [12], document_ids: [], chapter_id: 2 })
  })

  it('a new link while the page is open applies its subject, chapter and sources', async () => {
    mockApi()
    render(
      <MemoryRouter initialEntries={['/practice?subject=1']}>
        <Link to="/practice?subject=1&chapter=2&sources=all">Start a round</Link>
        <Routes>
          <Route path="/practice" element={<SetupPage />} />
        </Routes>
      </MemoryRouter>,
    )
    const select = await screen.findByLabelText('Chapter')
    await waitFor(() => expect(select).toHaveValue('1'))
    const group = screen.getByRole('radiogroup', { name: 'Questions' })
    await userEvent.click(within(group).getByRole('radio', { name: /My materials/ }))
    expect(within(group).getByRole('radio', { name: /My materials/ })).toBeChecked()
    await userEvent.click(screen.getByRole('link', { name: 'Start a round' }))
    await waitFor(() => expect(screen.getByLabelText('Chapter')).toHaveValue('2'))
    expect(within(group).getByRole('radio', { name: /All questions/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Whole chapter' })).toBeChecked()
  })
})
