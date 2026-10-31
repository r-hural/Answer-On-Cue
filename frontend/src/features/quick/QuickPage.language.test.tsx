import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { job, topic } from '../library/library.mocks'
import { LANGUAGES, languagesWith } from '../library/languages.mocks'
import { QuickPage } from './QuickPage'
import { NEED_PREMIUM_VOICE } from './quickState'
import { requestsTo, setupQuick, type QuickWorld } from './quick.mocks'

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/quick/5']}>
      <Routes>
        <Route path="/quick/:subjectId" element={<QuickPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

function ready(language: string | undefined, patch: Partial<QuickWorld> = {}) {
  return setupQuick({
    subject: { id: 5, name: 'Biology', description: null, document_count: 1, ...(language ? { language } : {}) },
    topics: [
      topic({ id: 11, title: 'Cell membrane', question_count: 3 }),
      topic({ id: 12, title: 'Transport', question_count: 3 }),
    ],
    terms: [{ term: 'phospholipid', definition: null, topic_id: 11 }],
    activeQuestions: 6,
    jobs: [
      job({ id: 8, kind: 'questions.generate', status: 'done' }),
      job({ id: 7, kind: 'topics.extract', status: 'done', steps_done: 4 }),
    ],
    ...patch,
  })
}

const audios = (world: QuickWorld) =>
  requestsTo(world, 'POST', /^\/api\/rounds$/).map((r) => (r.body as { config: { question_audio: unknown } }).config.question_audio)

beforeEach(() => {
  sessionStorage.clear()
})

describe('QuickPage: the language of the subject', () => {
  it('shows an English subject as en-GB and its file name and name field plain', async () => {
    ready('en')
    server.use(http.get('/api/languages', () => HttpResponse.json(languagesWith('en'))))
    renderPage()
    expect(await screen.findByText('6 questions are ready')).toBeInTheDocument()
    expect(screen.getByText('biologia.pdf')).not.toHaveAttribute('lang')
    expect(screen.getByLabelText('Subject name')).not.toHaveAttribute('lang')
    expect(screen.getByText('Read aloud (the language\'s default voice)')).toBeInTheDocument()
  })

  it('shows the topics and terms of an English subject as en-GB', async () => {
    ready('en', { jobs: [job({ id: 7, kind: 'topics.extract', status: 'done', steps_done: 4 })], activeQuestions: 0, topics: [topic({ id: 11, title: 'Cell membrane', question_count: 0 })] })
    renderPage()
    expect(await screen.findByText('Cell membrane')).toHaveAttribute('lang', 'en-GB')
    expect(screen.getByText('phospholipid')).toHaveAttribute('lang', 'en-GB')
  })

  it('asks for the premium voice and, refused for a language without one, starts again with the browser voice', async () => {
    const world = ready('en')
    let calls = 0
    server.use(
      http.post('/api/rounds', async ({ request }) => {
        calls += 1
        world.requests.push({ method: 'POST', path: '/api/rounds', body: await request.json() })
        if (calls === 1) return HttpResponse.json({ detail: NEED_PREMIUM_VOICE }, { status: 422 })
        return HttpResponse.json({ id: 77 }, { status: 201 })
      }),
    )
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Start' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/practice/77/microphone')
    expect(audios(world)).toEqual([{ mode: 'premium' }, { mode: 'browser' }])
  })

  it('shows the message after a second refusal, and does not retry a third time', async () => {
    let calls = 0
    ready('en')
    server.use(
      http.post('/api/rounds', () => {
        calls += 1
        return HttpResponse.json({ detail: NEED_PREMIUM_VOICE }, { status: 422 })
      }),
    )
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Start' }))
    expect(await screen.findByText(NEED_PREMIUM_VOICE)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled()
    expect(calls).toBe(2)
  })

  it('does not retry for another error', async () => {
    let calls = 0
    ready('en')
    server.use(
      http.post('/api/rounds', () => {
        calls += 1
        return HttpResponse.json({ detail: 'Not enough questions for this round.' }, { status: 409 })
      }),
    )
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Start' }))
    expect(await screen.findByText('Not enough questions for this round.')).toBeInTheDocument()
    expect(calls).toBe(1)
  })

  it('keeps Polish as it was: the premium request once and the global voice label', async () => {
    const world = ready(undefined)
    renderPage()
    expect(await screen.findByText('Read aloud (Piper, local voice)')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/practice/77/microphone')
    expect(audios(world)).toEqual([{ mode: 'premium' }])
  })

  it('names the voice set on the Polish language row', async () => {
    ready('pl')
    server.use(
      http.get('/api/languages', () =>
        HttpResponse.json(LANGUAGES.map((row) => (row.code === 'pl' ? { ...row, voice_provider: 'openai', voice_name: 'cedar' } : row))),
      ),
    )
    renderPage()
    expect(await screen.findByText('Read aloud (OpenAI Realtime voice)')).toBeInTheDocument()
  })
})
