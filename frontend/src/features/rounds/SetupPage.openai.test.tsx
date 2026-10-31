import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RoundEstimate } from '../costs/api'
import { roundEstimate } from '../costs/costs.mocks'
import { server } from '../../test/msw'
import { SetupPage } from './SetupPage'
import { setupRoundWorld, type RoundWorld } from './rounds.mocks'

const PREFS = {
  transcription_model: 'gpt-transcribe',
  generation_provider: 'openai',
  generation_model: 'gen-1',
  assessment_provider: 'anthropic',
  assessment_model: 'asm-1',
  voice_provider: 'piper',
  voice_name: 'pl_PL-bass-high',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
}

const VOICES = [
  { provider: 'piper', id: 'pl_PL-bass-high', label: 'Bass (Piper, local, high quality)', installed: true, gender: 'male' },
  { provider: 'openai', id: 'marin', label: 'Marin (OpenAI Realtime, recommended)', installed: true, gender: 'female' },
  { provider: 'openai', id: 'alloy', label: 'Alloy (OpenAI Realtime)', installed: false, gender: 'female' },
  { provider: 'browser', id: 'browser', label: 'Browser voice (built into the system)', installed: true, gender: null },
]

const topic = (id: number, title: string, count: number) => ({
  id,
  title,
  description: null,
  origin: 'extracted',
  status: 'active',
  objectives: [],
  passage_count: 3,
  question_count: count,
  children: [],
})

const question = (id: number, topicId: number) => ({
  version_id: id,
  family_id: id,
  version: 1,
  text: `Pytanie ${id}`,
  style: 'short',
  difficulty: 'recall',
  primary_topic_id: topicId,
  status: 'active',
})

function mockApi(
  prefs: Record<string, unknown> = PREFS,
  estimate: RoundEstimate = roundEstimate,
): { world: RoundWorld; previews: unknown[] } {
  const world = setupRoundWorld()
  const previews: unknown[] = []
  server.use(
    http.get('/api/subjects', () =>
      HttpResponse.json([{ id: 1, name: 'Demokracja w Atenach', description: null, document_count: 1 }]),
    ),
    http.get('/api/settings', () => HttpResponse.json(prefs)),
    http.get('/api/voice/voices', () => HttpResponse.json(VOICES)),
    http.get('/api/topics', () => HttpResponse.json([topic(11, 'Reformy', 6), topic(12, 'Ostracyzm', 6)])),
    http.get('/api/subjects/:id/documents', () =>
      HttpResponse.json([{ id: 21, title: 'Ateny', latest_version: { passage_count: 5 }, version_count: 1 }]),
    ),
    http.get('/api/questions', () =>
      HttpResponse.json(Array.from({ length: 12 }, (_, i) => question(i + 1, i < 6 ? 11 : 12))),
    ),
    http.get('/api/subjects/:id', () => HttpResponse.json({ id: 1, name: 'Demokracja w Atenach' })),
    http.get('/api/costs/estimate/round', () => HttpResponse.json(estimate)),
    http.post('/api/voice/preview', async ({ request }) => {
      previews.push(await request.json())
      return HttpResponse.json({ audio_url: '/api/voice/cache/abc.wav' })
    }),
  )
  return { world, previews }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/practice']}>
      <Routes>
        <Route path="/practice" element={<SetupPage />} />
        <Route path="/practice/:roundId/microphone" element={<p>Microphone page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function readAloud() {
  await screen.findByLabelText('Subject')
  return screen.getByLabelText('Read questions aloud') as HTMLSelectElement
}

beforeEach(() => {
  sessionStorage.clear()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('SetupPage: the OpenAI Realtime voice', () => {
  it('offers the OpenAI Realtime voice', async () => {
    mockApi()
    renderPage()
    const select = await readAloud()
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Off (text only)',
      'Piper voice (local)',
      'Azure voice',
      'OpenAI Realtime voice',
      'Browser voice',
    ])
  })

  it('lists only installed OpenAI voices', async () => {
    mockApi()
    renderPage()
    await userEvent.selectOptions(await readAloud(), 'openai')
    const voice = screen.getByLabelText('Voice') as HTMLSelectElement
    expect(within(voice).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Default voice',
      'Marin (OpenAI Realtime, recommended)',
    ])
  })

  it('previews and starts with the OpenAI voice', async () => {
    const { world, previews } = mockApi()
    const played: string[] = []
    vi.stubGlobal(
      'Audio',
      class {
        constructor(public src: string) {}
        play() {
          played.push(this.src)
          return Promise.resolve()
        }
      },
    )
    renderPage()
    await userEvent.selectOptions(await readAloud(), 'openai')
    await userEvent.selectOptions(screen.getByLabelText('Voice'), 'marin')
    await userEvent.click(screen.getByRole('button', { name: 'Preview voice' }))
    await waitFor(() => expect(played).toEqual(['/api/voice/cache/abc.wav']))
    expect(previews).toEqual([{ provider: 'openai', voice: 'marin', rate: 1, language: 'pl' }])
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    const start = screen.getByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await userEvent.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect((post?.body as { config: { question_audio: unknown } }).config.question_audio).toEqual({
      mode: 'premium',
      provider: 'openai',
      voice: 'marin',
      rate: 1,
    })
  })

  it('estimate line names OpenAI', async () => {
    mockApi(PREFS, {
      ...roundEstimate,
      voice_provider: 'openai',
      parts: [...roundEstimate.parts, { operation: 'voice.synthesize', usd: 0.01 }],
    })
    renderPage()
    const term = await screen.findByText('Estimated AI cost')
    await waitFor(() =>
      expect(term.nextElementSibling).toHaveTextContent('voice about $0.01 (OpenAI)'),
    )
  })

  it('OpenAI preferences preselect it', async () => {
    mockApi({ ...PREFS, voice_provider: 'openai', voice_name: 'marin' })
    renderPage()
    const select = await readAloud()
    await waitFor(() => expect(select).toHaveValue('openai'))
    expect(select.selectedOptions[0].textContent).toBe('OpenAI Realtime voice')
  })
})
