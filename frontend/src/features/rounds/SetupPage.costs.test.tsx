import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import type { RoundEstimate } from '../costs/api'
import { monthWithCeiling, roundEstimate } from '../costs/costs.mocks'
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
  voice_name: '',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
}

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

function mockApi(estimates: RoundEstimate[] = [roundEstimate], chapters = false): {
  world: RoundWorld
  queries: URLSearchParams[]
} {
  const world = setupRoundWorld()
  const queries: URLSearchParams[] = []
  server.use(
    http.get('/api/subjects', () =>
      HttpResponse.json([
        { id: 1, name: 'Demokracja w Atenach', description: null, document_count: 1, chapter_count: chapters ? 2 : 1 },
        { id: 2, name: 'Biologia', description: null, document_count: 1 },
      ]),
    ),
    http.get('/api/subjects/:id/chapters', () =>
      HttpResponse.json([
        { id: 5, subject_id: 1, name: 'Grecja', ordinal: 0, is_default: true, document_count: 1, topic_count: 1, question_count: 6 },
        { id: 6, subject_id: 1, name: 'Rzym', ordinal: 1, is_default: false, document_count: 0, topic_count: 1, question_count: 6 },
      ]),
    ),
    http.get('/api/settings', () => HttpResponse.json(PREFS)),
    http.get('/api/voice/voices', () => HttpResponse.json([])),
    http.get('/api/topics', () => HttpResponse.json([topic(11, 'Reformy', 6), topic(12, 'Ostracyzm', 6)])),
    http.get('/api/subjects/:id/documents', () =>
      HttpResponse.json([{ id: 21, title: 'Ateny', latest_version: { passage_count: 5 }, version_count: 1 }]),
    ),
    http.get('/api/questions', () =>
      HttpResponse.json(Array.from({ length: 12 }, (_, i) => question(i + 1, i < 6 ? 11 : 12))),
    ),
    http.get('/api/costs/estimate/round', ({ request }) => {
      queries.push(new URL(request.url).searchParams)
      return HttpResponse.json(estimates[Math.min(queries.length - 1, estimates.length - 1)])
    }),
  )
  return { world, queries }
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

const summary = () => screen.getByRole('complementary', { name: 'This round' })

async function estimateRow() {
  const term = await screen.findByText('Estimated AI cost')
  return term.nextElementSibling as HTMLElement
}

beforeEach(() => {
  sessionStorage.clear()
})

describe('SetupPage: the estimated AI cost', () => {
  it('adds the range and what it is based on to the summary', async () => {
    mockApi([{ ...roundEstimate, month: monthWithCeiling }])
    renderPage()
    const dd = await estimateRow()
    expect(dd.tagName).toBe('DD')
    expect(within(dd).getByText('$0.45–0.55')).toHaveClass('num')
    expect(within(dd).getByText('$0.45–0.55').tagName).toBe('STRONG')
    expect(dd.querySelector('small.muted')).toHaveTextContent(
      'Transcription about 25 min, assessment, follow-ups; voice free (Piper). This month: $2.40 of $15.00.',
    )
    expect(summary().querySelector('dl.facts')).toContainElement(dd)
  })

  it('says when the estimate is based on your last answers', async () => {
    mockApi([{ ...roundEstimate, basis: 'measured' }])
    renderPage()
    const dd = await estimateRow()
    expect(dd).toHaveTextContent('This month: $2.40. Based on your last answers.')
  })

  it('asks again when the count, answer time, follow-ups or subject change', async () => {
    const { queries } = mockApi()
    renderPage()
    await estimateRow()
    await waitFor(() => expect(queries.length).toBeGreaterThan(0))
    const last = () => queries[queries.length - 1]
    expect(last().get('subject_id')).toBe('1')
    const user = userEvent.setup()

    const count = screen.getByLabelText('Main questions')
    await user.clear(count)
    await user.type(count, '6')
    await user.tab()
    await waitFor(() => expect(last().get('question_count')).toBe('6'))

    const answer = screen.getByLabelText('Answer time (s)')
    await user.clear(answer)
    await user.type(answer, '90')
    await user.tab()
    await waitFor(() => expect(last().get('answer_seconds')).toBe('90'))

    const followUps = screen.getByRole('checkbox', { name: /follow-up/i })
    const before = last().get('follow_ups')
    await user.click(followUps)
    await waitFor(() => expect(last().get('follow_ups')).not.toBe(before))

    await user.selectOptions(screen.getByLabelText('Subject'), '2')
    await waitFor(() => expect(last().get('subject_id')).toBe('2'))
  })

  it('shows the budget notice and disables Start when the round does not fit', async () => {
    const month = { ...monthWithCeiling, spent_usd: 14.82, remaining_usd: 0.18 }
    mockApi([{ ...roundEstimate, month, fits: false }])
    const { container } = renderPage()
    const title = await screen.findByText('The monthly spending ceiling is reached.')
    const notice = title.closest('.notice') as HTMLElement
    expect(notice).toHaveClass('error')
    expect(notice).toHaveTextContent(
      'October so far: $14.82 of $15.00. This round needs about $0.45–0.55, so it cannot start. Rounds and feedback you already have stay available.',
    )
    expect(within(notice).getByRole('link', { name: 'Change the ceiling' })).toHaveAttribute('href', '/settings#spending')
    expect(within(notice).getByRole('link', { name: 'See usage' })).toHaveAttribute('href', '/settings/usage')
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeDisabled()
    expect(container.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it("shows the server's message when the start is refused for the budget", async () => {
    const { world } = mockApi()
    const message = 'This round needs about $0.45–0.55 of AI usage, but only $0.10 is left of your $15.00 monthly ceiling.'
    world.createError = { status: 409, detail: { message, estimate: roundEstimate } }
    renderPage()
    await estimateRow()
    const start = screen.getByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await userEvent.setup().click(start)
    const title = await screen.findByText('The monthly spending ceiling is reached.')
    expect(title.closest('.notice')).toHaveTextContent(message)
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeDisabled()
    expect(screen.queryByText('Microphone page')).toBeNull()
  })

  it('enables Start again when the estimate reload after a refusal fails', async () => {
    const { world } = mockApi()
    const message = 'This round needs about $0.45–0.55 of AI usage, but only $0.10 is left of your $15.00 monthly ceiling.'
    world.createError = { status: 409, detail: { message, estimate: roundEstimate } }
    renderPage()
    await estimateRow()
    const user = userEvent.setup()
    const start = () => screen.getByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start()).toBeEnabled())
    await user.click(start())
    await screen.findByText('The monthly spending ceiling is reached.')
    expect(start()).toBeDisabled()

    server.use(http.get('/api/costs/estimate/round', () => new HttpResponse(null, { status: 500 })))
    const count = screen.getByLabelText('Main questions')
    await user.clear(count)
    await user.type(count, '6')
    await user.tab()
    await waitFor(() => expect(start()).toBeEnabled())
    expect(screen.queryByText('The monthly spending ceiling is reached.')).toBeNull()
  })

  it('works the same for a chaptered subject', async () => {
    const { queries } = mockApi([roundEstimate], true)
    renderPage()
    await estimateRow()
    expect(queries[queries.length - 1].get('subject_id')).toBe('1')
  })
})
