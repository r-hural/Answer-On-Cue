import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
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

const question = (id: number, policy: string) => ({
  version_id: id,
  family_id: id,
  version: 1,
  text: `Pytanie ${id}`,
  style: 'open',
  difficulty: 'recall',
  primary_topic_id: 11,
  status: 'active',
  source_policy: policy,
})

function mockApi(questions: ReturnType<typeof question>[]): RoundWorld {
  const world = setupRoundWorld()
  server.use(
    http.get('/api/subjects', () => HttpResponse.json([{ id: 1, name: 'Historia PL', description: null, document_count: 1 }])),
    http.get('/api/settings', () => HttpResponse.json(PREFS)),
    http.get('/api/voice/voices', () => HttpResponse.json([])),
    http.get('/api/topics', () =>
      HttpResponse.json([
        { id: 11, title: 'Ateny', description: null, origin: 'extracted', status: 'active', objectives: [], passage_count: 3, question_count: questions.length, children: [] },
      ]),
    ),
    http.get('/api/subjects/:id/documents', () => HttpResponse.json([])),
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

beforeEach(() => sessionStorage.clear())

const group = () => screen.getByRole('radiogroup', { name: 'Questions' })

describe('SetupPage: practice sources', () => {
  it('offers My materials and All questions, All questions checked, one primary button', async () => {
    mockApi([question(1, 'materials')])
    renderPage()
    await screen.findByLabelText('Subject')
    const radios = within(group()).getAllByRole('radio')
    expect(radios.map((radio) => radio.closest('label')!.querySelector('strong')!.textContent)).toEqual([
      'My materials',
      'All questions',
    ])
    expect(within(group()).getByRole('radio', { name: /All questions/ })).toBeChecked()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it.each([
    ['materials', /My materials/],
    ['expanded', /All questions/],
    ['all', /All questions/],
  ])('?sources=%s checks the matching option', async (value, name) => {
    mockApi([question(1, 'materials')])
    renderPage(`/practice?sources=${value}`)
    await screen.findByLabelText('Subject')
    expect(within(group()).getByRole('radio', { name })).toBeChecked()
  })

  it('a subject whose 17 questions are all "My materials + expand" offers them under All questions', async () => {
    const world = mockApi(Array.from({ length: 17 }, (_, i) => question(i + 1, 'expanded')))
    renderPage()
    await screen.findByLabelText('Subject')
    expect(await screen.findByText('(17 available)')).toBeInTheDocument()
    const start = screen.getByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await userEvent.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect(post?.body).toMatchObject({ config: { source_policy: 'all' } })
  })

  it('My materials counts only materials questions', async () => {
    mockApi([...Array.from({ length: 3 }, (_, i) => question(i + 1, 'materials')), question(9, 'outside')])
    renderPage()
    await screen.findByLabelText('Subject')
    await userEvent.click(within(group()).getByRole('radio', { name: /My materials/ }))
    expect(await screen.findByText('(3 available)')).toBeInTheDocument()
  })
})
