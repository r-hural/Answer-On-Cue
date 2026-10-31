import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { timeOnly } from '../rounds/feedback/format'
import { historyRow, setupReportsWorld } from '../rounds/reports.mocks'
import { collectFeatures } from '../../app/router'
import type { FeatureModule } from '../../app/types'
import { HistoryPage } from './HistoryPage'
import * as progressRoutes from './routes'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/progress/history']}>
      <Routes>
        <Route path="/progress/history" element={<HistoryPage />} />
        <Route path="/rounds/:id/summary" element={<p>Summary page</p>} />
        <Route path="/rounds/:id" element={<p>Exam page</p>} />
        <Route path="/practice" element={<p>Practice page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

function mockSubjects() {
  server.use(
    http.get('/api/subjects', () =>
      HttpResponse.json([
        { id: 1, name: 'Demokracja ateńska', description: null, document_count: 2 },
        { id: 2, name: 'Biologia komórki', description: null, document_count: 1 },
      ]),
    ),
  )
}

const BREAKABLE = /(^|[\s(„"«])[aiouwzAIOUWZ] /

const ROWS = [
  historyRow({ round_id: 9 }),
  historyRow({
    round_id: 8,
    subject_id: 2,
    subject_name: 'Biologia komórki',
    feedback_timing: 'exam',
    total: 8,
    asked: 8,
    primary_score: 71,
    band_distribution: { weak: 0, needs_improvement: 2, good: 5, very_good: 1 },
    median_preparation_used_ms: 6000,
    mean_fluency: 72,
  }),
  historyRow({
    round_id: 7,
    status: 'abandoned',
    asked: 4,
    primary_score: 61,
    provisional: true,
    band_distribution: { weak: 0, needs_improvement: 2, good: 1, very_good: 0 },
    median_preparation_used_ms: 8000,
    mean_fluency: null,
  }),
  historyRow({
    round_id: 6,
    status: 'active',
    feedback_timing: 'exam',
    finished_at: null,
    asked: 3,
    primary_score: null,
    withheld: true,
    band_distribution: { weak: 0, needs_improvement: 0, good: 0, very_good: 0 },
    median_preparation_used_ms: null,
    mean_fluency: null,
  }),
]

describe('HistoryPage', () => {
  it('lists the rounds with score, bands, preparation median and fluency', async () => {
    mockSubjects()
    setupReportsWorld({ history: ROWS })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Progress' })).toBeInTheDocument()
    const items = [...document.querySelectorAll('.rows.rounds > li')] as HTMLElement[]
    expect(items).toHaveLength(4)

    const first = items[0]
    const link = within(first).getByRole('link')
    expect(link.textContent).toMatch(/^\d{1,2} \w{3}, \d{2}:\d{2}$/)
    expect(link).toHaveAttribute('href', '/rounds/9/summary')
    const subject = first.querySelector('.subject') as HTMLElement
    expect(subject).toHaveTextContent('Demokracja ateńska')
    expect(subject).toHaveClass('content')
    expect(subject).not.toHaveAttribute('lang')
    expect(first.querySelector('.meta')).toHaveTextContent('Standard · Practice10 questions')
    expect(within(first).getByText('Knowledge and language').nextElementSibling).toHaveTextContent('64')
    const counts = [...first.querySelectorAll('.band-counts span')]
    expect(counts.map((c) => c.className)).toEqual(['weak', 'needs', 'good', 'very-good'])
    expect(counts.map((c) => c.textContent)).toEqual(['2 Weak', '2 Needs improvement', '5 Good', '1 Very good'])
    expect(counts[0].querySelector('i')).toHaveClass('visually-hidden')
    expect(within(first).getByText('Prep. median').nextElementSibling).toHaveTextContent('11 s')
    expect(within(first).getByText('Fluency').nextElementSibling).toHaveTextContent('66')
    expect(first).not.toHaveTextContent('provisional')
    expect(first.querySelector('.chip')).toBeNull()
  })

  it('marks an ended-early round, shows its provisional score and "—" for a missing fluency', async () => {
    mockSubjects()
    setupReportsWorld({ history: ROWS })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const early = ([...document.querySelectorAll('.rows.rounds > li')] as HTMLElement[])[2]
    expect(within(early).getByText('Ended early')).toHaveClass('chip', 'warn')
    expect(early.querySelector('.meta')).toHaveTextContent('4 of 10 questions')
    const score = within(early).getByText('Knowledge and language').nextElementSibling as HTMLElement
    expect(score).toHaveTextContent('61 provisional')
    expect(score.querySelector('small')).toHaveTextContent('provisional')
    expect(within(early).getByText('Fluency').nextElementSibling).toHaveTextContent('—')
    expect(within(early).getByRole('link')).toHaveAttribute('href', '/rounds/7/summary')
    const zeros = early.querySelectorAll('.band-counts .zero')
    expect([...zeros].map((z) => z.className)).toEqual(['weak zero', 'very-good zero'])
  })

  it('links an unfinished round to the exam screen and shows no score', async () => {
    mockSubjects()
    setupReportsWorld({ history: ROWS })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const open = ([...document.querySelectorAll('.rows.rounds > li')] as HTMLElement[])[3]
    expect(within(open).getByText('Not finished')).toHaveClass('chip', 'warn')
    expect(within(open).getByRole('link')).toHaveAttribute('href', '/rounds/6')
    expect(within(open).getByText('Knowledge and language').nextElementSibling).toHaveTextContent(/^—$/)
    expect(within(open).getByText('Prep. median').nextElementSibling).toHaveTextContent(/^—$/)
    expect(open.querySelector('.meta')).toHaveTextContent('Standard · Exam3 of 10 questions')
  })

  it('filters by subject and by feedback timing through the query', async () => {
    mockSubjects()
    const world = setupReportsWorld({ history: ROWS })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const user = userEvent.setup()
    await waitFor(() => expect(within(screen.getByLabelText('Subject')).getAllByRole('option')).toHaveLength(3))
    await user.selectOptions(screen.getByLabelText('Subject'), 'Biologia komórki')
    await waitFor(() => expect(document.querySelectorAll('.rows.rounds > li')).toHaveLength(1))
    expect(world.historyQueries.at(-1)).toBe('?subject_id=2')
    await user.selectOptions(screen.getByLabelText('Subject'), 'All subjects')
    await user.selectOptions(screen.getByLabelText('Feedback'), 'Exam')
    await waitFor(() => expect(document.querySelectorAll('.rows.rounds > li')).toHaveLength(2))
    expect(world.historyQueries.at(-1)).toBe('?feedback_timing=exam')
    await user.selectOptions(screen.getByLabelText('Subject'), 'Biologia komórki')
    await waitFor(() => expect(world.historyQueries.at(-1)).toBe('?subject_id=2&feedback_timing=exam'))
    expect(screen.getByLabelText('Subject').querySelector('option[value="2"]')).not.toHaveAttribute('lang')
  })

  it('has the Coverage and History tabs and the band key', async () => {
    mockSubjects()
    setupReportsWorld({ history: ROWS })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const tabs = within(screen.getByRole('navigation', { name: 'Progress views' }))
    expect(tabs.getAllByRole('link').map((link) => link.textContent)).toEqual(['Coverage', 'History', 'Trends'])
    expect(tabs.getByRole('link', { name: 'History' })).toHaveAttribute('aria-current', 'page')
    expect(tabs.getByRole('link', { name: 'Coverage' })).not.toHaveAttribute('aria-current')
    expect(tabs.getByRole('link', { name: 'Trends' })).not.toHaveAttribute('aria-current')
    const key = within(screen.getByLabelText('Band key'))
    expect(key.getByText('Weak')).toHaveClass('band', 'weak')
    expect(key.getByText('Very good')).toHaveClass('band', 'very-good')
    expect(screen.queryByText('AI cost')).not.toBeInTheDocument()
  })

  it('shows an empty state with a New round link', async () => {
    mockSubjects()
    setupReportsWorld({ history: [] })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'No rounds yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'New round' })).toHaveAttribute('href', '/practice')
    expect(screen.queryByLabelText('Band key')).not.toBeInTheDocument()
  })

  it('says so when no round matches the filters', async () => {
    mockSubjects()
    setupReportsWorld({ history: [ROWS[0]] })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    await userEvent.selectOptions(screen.getByLabelText('Feedback'), 'Exam')
    expect(await screen.findByRole('heading', { name: 'No rounds match these filters.' })).toBeInTheDocument()
  })

  it('says so when the history cannot be loaded', async () => {
    mockSubjects()
    server.use(http.get('/api/reports/history', () => HttpResponse.json({ detail: 'Database busy.' }, { status: 500 })))
    renderPage()
    expect(await screen.findByText('Database busy.')).toBeInTheDocument()
  })

  it('keeps the subject name free of breakable one-letter words, and without a lang', async () => {
    mockSubjects()
    setupReportsWorld({ history: [historyRow({ subject_name: 'Historia w Atenach i Sparcie' })] })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const names = [...document.querySelectorAll('.subject.content')]
    expect(names.length).toBeGreaterThanOrEqual(1)
    for (const node of names) {
      expect(node).not.toHaveAttribute('lang')
      expect(node.textContent ?? '').not.toMatch(BREAKABLE)
    }
  })
})

describe('progress routes', () => {
  it('adds the Progress nav item and the progress routes', () => {
    const { routes, nav } = collectFeatures({ progress: progressRoutes as unknown as FeatureModule })
    expect(nav).toEqual([{ path: '/progress', label: 'Progress', order: 30 }])
    expect(routes.map((route) => route.path)).toEqual([
      'progress',
      'progress/coverage',
      'progress/topics/:topicId',
      'progress/history',
      'progress/trends',
    ])
  })

  it('keeps /progress landing on the history', () => {
    const redirect = progressRoutes.routes.find((route) => route.path === 'progress')
    const element = redirect?.element as { props: { to: string; replace: boolean } }
    expect(element.props).toMatchObject({ to: '/progress/history', replace: true })
  })
})

describe('HistoryPage: source policy', () => {
  const EXPANDED = historyRow({ round_id: 30, source_policy: 'expanded' })
  const STRICT = historyRow({ round_id: 31, source_policy: 'materials' })

  function mockHistory(rows = [EXPANDED, STRICT]) {
    const queries: string[] = []
    server.use(
      http.get('/api/reports/history', ({ request }) => {
        const url = new URL(request.url)
        queries.push(url.search)
        const policy = url.searchParams.get('source_policy')
        return HttpResponse.json(rows.filter((row) => !policy || (row.source_policy ?? 'materials') === policy))
      }),
    )
    return queries
  }

  it('filters by Sources through the query', async () => {
    mockSubjects()
    const queries = mockHistory()
    renderPage()
    await waitFor(() => expect(document.querySelectorAll('.rows.rounds > li')).toHaveLength(2))
    const select = screen.getByLabelText('Sources')
    expect(select).toHaveAttribute('id', 'f-sources')
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'All sources',
      'My materials',
      'All questions',
    ])
    await userEvent.selectOptions(select, 'My materials')
    await waitFor(() => expect(document.querySelectorAll('.rows.rounds > li')).toHaveLength(1))
    expect(queries.at(-1)).toBe('?source_policy=materials')
  })

  it('ends an expanded round meta with Expand the topic and leaves a strict one unchanged', async () => {
    mockSubjects()
    mockHistory()
    renderPage()
    await waitFor(() => expect(document.querySelectorAll('.rows.rounds > li')).toHaveLength(2))
    const [expanded, strict] = [...document.querySelectorAll('.rows.rounds > li .meta > span:first-child')]
    expect(expanded.textContent).toBe('Standard · Practice · My materials + expand')
    expect(strict.textContent).toBe('Standard · Practice · My materials')
  })

  it('says so when no round matches the Sources filter', async () => {
    mockSubjects()
    mockHistory([STRICT])
    renderPage()
    await waitFor(() => expect(document.querySelectorAll('.rows.rounds > li')).toHaveLength(1))
    await userEvent.selectOptions(screen.getByLabelText('Sources'), 'All questions')
    expect(await screen.findByRole('heading', { name: 'No rounds match these filters.' })).toBeInTheDocument()
  })
})

describe('HistoryPage: round types', () => {
  it('labels an adaptive round; a row without the field is Standard', async () => {
    const legacy = historyRow({ round_id: 4 })
    delete (legacy as { round_type?: string }).round_type
    setupReportsWorld({ history: [historyRow({ round_id: 3, feedback_timing: 'exam', round_type: 'adaptive' }), legacy] })
    renderPage()
    await screen.findByRole('heading', { level: 1, name: 'Progress' })
    const items = [...document.querySelectorAll('.rows.rounds > li')] as HTMLElement[]
    expect(items).toHaveLength(2)
    expect(items[0].querySelector('.meta span')).toHaveTextContent(/^Adaptive · Exam$/)
    expect(items[1].querySelector('.meta span')).toHaveTextContent(/^Standard · Practice$/)
  })

  it('an adaptive expanded round names both', async () => {
    setupReportsWorld({
      history: [historyRow({ round_id: 5, feedback_timing: 'exam', round_type: 'adaptive', source_policy: 'expanded' })],
    })
    renderPage()
    await screen.findByRole('heading', { level: 1, name: 'Progress' })
    const item = document.querySelector('.rows.rounds > li') as HTMLElement
    expect(item.querySelector('.meta span')).toHaveTextContent(/^Adaptive · Exam · My materials \+ expand$/)
  })
})

describe('HistoryPage: retry rounds', () => {
  it('names a retry round by the time of its original round', async () => {
    mockSubjects()
    const parent = '2026-10-22T16:05:00Z'
    setupReportsWorld({
      history: [
        historyRow({ round_id: 12, round_type: 'retry', parent_round_id: 5, parent_started_at: parent, total: 4, asked: 4 }),
        historyRow({ round_id: 5 }),
      ],
    })
    renderPage()
    const meta = (await screen.findByText(`Retry round of ${timeOnly(parent)}`)).closest('.meta') as HTMLElement
    expect(within(meta).getByText('4 questions')).toBeInTheDocument()
    expect(screen.getAllByText(/^Retry round of/)).toHaveLength(1)
  })
})
