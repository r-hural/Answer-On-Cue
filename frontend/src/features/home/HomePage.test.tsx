import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { HomePage } from './HomePage'
import { homeStrings } from './strings'

interface Seen {
  method: string
  path: string
  body: unknown
}

function backend(hasKey: boolean, seen: Seen[] = []) {
  server.use(
    http.get('/api/providers/status', () =>
      HttpResponse.json({
        keys: [
          { provider: 'openai', configured: hasKey, masked: hasKey ? '••••abcd' : null, region: null },
        ],
        roles: { generation: { provider: 'openai', model: 'm' } },
      }),
    ),
    http.get('/api/subjects', () =>
      HttpResponse.json([
        { id: 1, name: 'Historia', description: null, document_count: 1 },
        { id: 2, name: 'Biologia', description: null, document_count: 0 },
      ]),
    ),
    http.get('/api/rounds', ({ request }) => {
      const subject = new URL(request.url).searchParams.get('subject_id')
      const round = (id: number, started: string, status: string, round_type = 'standard') => ({
        id,
        status,
        started_at: started,
        finished_at: null,
        total: 10,
        done: 3,
        feedback_timing: 'practice',
        round_type,
      })
      return HttpResponse.json(
        subject === '1'
          ? [round(9, '2026-10-21T17:10:00Z', 'finished', 'adaptive'), round(8, '2026-10-20T16:00:00Z', 'paused')]
          : [round(7, '2026-10-18T15:00:00Z', 'abandoned')],
      )
    }),
    http.get('/api/rounds/8', () =>
      HttpResponse.json({ id: 8, phase: 'preparation', current_attempt: { id: 31 } }),
    ),
    http.post('/api/rounds/8/actions', async ({ request }) => {
      seen.push({ method: 'POST', path: '/api/rounds/8/actions', body: await request.json() })
      return HttpResponse.json({ id: 8, status: 'abandoned' })
    }),
  )
}

function renderHome() {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/rounds/:id/summary" element={<p>Round summary page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('HomePage without a key', () => {
  it('shows the first steps and the data notice', async () => {
    backend(false)
    renderHome()
    expect(await screen.findByRole('heading', { name: 'Welcome to AnswerOnCue' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add an API key' })).toHaveAttribute('href', '/settings#keys')
    expect(screen.getByRole('link', { name: 'Choose a voice' })).toHaveAttribute('href', '/settings#languages')
    expect(screen.getByRole('link', { name: 'Check the microphone' })).toHaveAttribute('href', '/microphone')
    expect(screen.getByRole('link', { name: 'Create a subject' })).toHaveAttribute('href', '/library')
    expect(screen.getByRole('link', { name: 'Quick practice' })).toHaveAttribute('href', '/quick')
    expect(screen.getByRole('heading', { name: 'Your data' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Privacy details' })).toHaveAttribute('href', '/settings#privacy')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(screen.queryByRole('link', { name: 'Historia' })).toBeNull()
  })
})

describe('HomePage while loading', () => {
  it('shows only the loading line until it knows whether a key is set', async () => {
    backend(false)
    renderHome()
    expect(screen.getByText('Loading…')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Start a round' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Home' })).toBeNull()
    expect(await screen.findByRole('heading', { name: 'Welcome to AnswerOnCue' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Start a round' })).toBeNull()
  })

  it('shows the error instead of loading forever when the data cannot be read', async () => {
    backend(true)
    server.use(
      http.get('/api/subjects', () => HttpResponse.json({ detail: 'Database locked.' }, { status: 500 })),
    )
    renderHome()
    expect(await screen.findByRole('alert')).toHaveTextContent('Database locked.')
    expect(screen.queryByText('Loading…')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Start a round' })).toBeNull()
  })
})

describe('HomePage with a key', () => {
  it('has "Start a round" as the only primary action and the quick practice entry', async () => {
    backend(true)
    renderHome()
    expect(await screen.findByRole('heading', { level: 1, name: 'Home' })).toBeInTheDocument()
    const start = screen.getByRole('link', { name: 'Start a round' })
    expect(start).toHaveAttribute('href', '/practice')
    expect(start).toHaveClass('primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    for (const link of screen.getAllByRole('link', { name: 'Quick practice from a file' }))
      expect(link).toHaveAttribute('href', '/quick')
    expect(screen.queryByText('Connected to the server')).toBeNull()
  })

  it('lists subjects with Practise or Open and polish names', async () => {
    backend(true)
    renderHome()
    const name = await screen.findByRole('link', { name: 'Historia' })
    expect(name).toHaveAttribute('href', '/library/1')
    expect(name).not.toHaveAttribute('lang')
    const first = name.closest('li')!
    expect(within(first).getByText('1 document')).toBeInTheDocument()
    expect(within(first).getByText(/^Last practised \d{1,2} Oct$/)).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: 'Practise' })).toHaveAttribute('href', '/practice?subject=1')
    const second = screen.getByRole('link', { name: 'Biologia' }).closest('li')!
    expect(within(second).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/library/2')
    expect(screen.getByRole('link', { name: 'All subjects in the Library' })).toHaveAttribute(
      'href',
      '/library',
    )
  })

  it('lists recent rounds with their links and chips', async () => {
    backend(true)
    renderHome()
    await screen.findByRole('link', { name: 'Historia' })
    const recent = screen.getByRole('region', { name: 'Recent rounds' })
    const links = within(recent).getAllByRole('link', { name: /^\d{1,2} [A-Z][a-z]{2}, \d{2}:\d{2}$/ })
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/rounds/9/summary',
      '/rounds/8',
      '/rounds/7/summary',
    ])
    expect(within(recent).getByText('Unfinished')).toBeInTheDocument()
    expect(within(recent).getByText('Ended early')).toBeInTheDocument()
    expect(within(recent).getAllByText('Standard · Practice')).toHaveLength(2)
    expect(within(recent).getByText('Adaptive · Practice')).toBeInTheDocument()
    expect(within(recent).getByRole('link', { name: 'History' })).toHaveAttribute(
      'href',
      '/progress/history',
    )
  })

  it('ends an unfinished round from the notice', async () => {
    const seen: Seen[] = []
    backend(true, seen)
    renderHome()
    const notice = await screen.findByRole('status')
    expect(within(notice).getByText('Unfinished round')).toBeInTheDocument()
    expect(within(notice).getByText('„Historia”')).not.toHaveAttribute('lang')
    expect(within(notice).getByText(/question 4 of 10/)).toBeInTheDocument()
    expect(within(notice).getByRole('link', { name: 'Resume round' })).toHaveAttribute('href', '/rounds/8')
    await userEvent.click(within(notice).getByRole('button', { name: 'End round and see results' }))
    expect(await screen.findByText('Round summary page')).toBeInTheDocument()
    expect(seen[0].body).toEqual({ action: 'abandon', from_phase: 'preparation', attempt_id: 31 })
  })

  it('shows an error notice when the server is down', async () => {
    backend(true)
    server.use(http.get('/api/health', () => new HttpResponse(null, { status: 500 })))
    renderHome()
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the server')
  })

  it('keeps the page usable when the rounds of a subject cannot be read', async () => {
    backend(true)
    server.use(http.get('/api/rounds', () => new HttpResponse(null, { status: 500 })))
    renderHome()
    await screen.findByRole('link', { name: 'Historia' })
    await waitFor(() => expect(screen.getByText('No rounds yet.')).toBeInTheDocument())
  })
})

describe('home strings: round kinds', () => {
  it('names a retry round', () => {
    expect(homeStrings.roundKind('practice', 'retry')).toBe('Retry round · Practice')
    expect(homeStrings.roundKind('exam', 'adaptive')).toBe('Adaptive · Exam')
  })
})
