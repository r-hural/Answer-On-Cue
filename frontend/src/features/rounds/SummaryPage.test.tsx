import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { SummaryPage } from './SummaryPage'
import type { ExtensionOffer, RetryOffer } from './api'
import type { RoundReport } from './reports'
import { config } from './rounds.mocks'
import {
  delivery,
  followUpSeries,
  item,
  knowledge,
  provisionalRoundReport,
  roundReport,
  roundReportWithFollowUps,
  setupReportsWorld,
} from './reports.mocks'

function renderPage(pollMs = 20) {
  return render(
    <MemoryRouter initialEntries={['/rounds/5/summary']}>
      <Routes>
        <Route path="/rounds/:roundId/summary" element={<SummaryPage pollMs={pollMs} />} />
        <Route path="/progress/history" element={<p>History page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.useRealTimers()
})

const BREAKABLE = /(^|[\s(„"«])[aiouwzAIOUWZ] /
const fact = (label: string) => screen.getByText(label, { selector: 'dt' }).nextElementSibling as HTMLElement

function endedEarly(): RoundReport {
  return roundReport({
    status: 'abandoned',
    finished_at: '2026-10-21T17:00:00Z',
    provisional: true,
    asked: 1,
    total: 3,
    knowledge: knowledge({
      primary_score: 80,
      band_distribution: { weak: 0, needs_improvement: 0, good: 1, very_good: 0 },
      provisional: false,
    }),
    items: [
      item({ band: 'good' }),
      item({ ordinal: 2, item_id: 51, attempt_id: null, status: 'not_asked', outcome: 'pending', band: null, factual: null, total: null }),
      item({ ordinal: 3, item_id: 52, attempt_id: null, status: 'not_asked', outcome: 'pending', band: null, factual: null, total: null }),
    ],
    restarted_ordinals: [],
    pause_count: 0,
    paused_ms: 0,
  })
}

describe('SummaryPage: a finished round', () => {
  it('shows the headline, the meta line and the component averages', async () => {
    setupReportsWorld({ rounds: [roundReport()] })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Round finished' })).toBeInTheDocument()
    const head = (screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement).textContent as string
    expect(head).toMatch(/Demokracja ateńska · Standard · Practice feedback · My materials · \d{1,2} \w{3}, \d{2}:\d{2}–\d{2}:\d{2} · 4 questions/)
    expect(screen.getByText('Demokracja ateńska')).not.toHaveAttribute('lang')
    expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'New round' })).toHaveAttribute('href', '/practice')
    expect((document.querySelector('.crumbs') as HTMLElement).textContent).toMatch(/^History › \d{1,2} \w{3}, \d{2}:\d{2}$/)

    expect(document.querySelector('.headline b')).toHaveTextContent('64')
    expect(screen.getByText('Average of 4 main questions; “I don\'t know” and skipped count as 0.')).toBeInTheDocument()
    expect(fact('Correctness')).toHaveTextContent('71')
    expect(fact('Completeness')).toHaveTextContent('58')
    expect(fact('Factual')).toHaveTextContent('63')
    expect(fact('Language')).toHaveTextContent('69 (8 answers assessable)')
    expect(fact('Language').querySelector('small')).toHaveTextContent('(8 answers assessable)')
    expect(screen.queryByText('Provisional')).not.toBeInTheDocument()
  })

  it('draws the band bar by count and lists every band, zero included', async () => {
    setupReportsWorld({
      rounds: [roundReport({ knowledge: knowledge({ band_distribution: { weak: 2, needs_improvement: 0, good: 5, very_good: 1 } }) })],
    })
    renderPage()
    const bar = await screen.findByRole('img', { name: 'Answers per band: 2 Weak, 0 Needs improvement, 5 Good, 1 Very good' })
    const segments = [...bar.querySelectorAll('span')]
    expect(segments.map((s) => s.textContent)).toEqual(['2', '5', '1'])
    expect(segments.map((s) => s.className)).toEqual(['weak', 'good', 'very-good'])
    expect(segments[1]).toHaveStyle({ flexGrow: '5' })
    const list = within(screen.getByRole('list', { name: 'Answers per band' }))
    expect(list.getByText('2 Weak')).toHaveClass('band', 'weak')
    expect(list.getByText('0 Needs improvement')).toHaveClass('band', 'needs')
    expect(list.getByText('5 Good')).toBeInTheDocument()
    expect(list.getByText('1 Very good')).toHaveClass('band', 'very-good')
  })

  it('shows the speed and fluency medians with their counts', async () => {
    setupReportsWorld({ rounds: [roundReport()] })
    renderPage()
    await screen.findByRole('heading', { name: 'Speed and fluency' })
    expect(fact('Preparation used (median)')).toHaveTextContent('11 s')
    expect(fact('Start of speech (median)')).toHaveTextContent('0.8 s')
    expect(fact('Start of speech (median)').querySelector('small')).toHaveTextContent('(3 answers)')
    expect(fact('Total time before speaking (median)')).toHaveTextContent('12 s')
    expect(fact('Total time before speaking (median)').querySelector('small')).toHaveTextContent('Good and Very good answers only: 14 s')
    expect(fact('Targets met')).toHaveTextContent('6 of 8 answers (start of speech within 3 s)')
    expect(fact('Longest pause (median)')).toHaveTextContent('3.2 s')
    expect(fact('Long pauses per minute of speech')).toHaveTextContent('1.4')
    expect(fact('Silence while speaking')).toHaveTextContent('16%')
    expect(fact('Fluency (average)')).toHaveTextContent('66 (7 answers assessable)')
  })

  it('says "unavailable" for what could not be measured, never a zero', async () => {
    setupReportsWorld({
      rounds: [
        roundReport({
          knowledge: knowledge({ mean_language: null, language_assessable: 0, mean_fluency: null, fluency_assessable: 0 }),
          delivery: delivery({
            median_preparation_used_ms: null,
            median_speech_start_latency_ms: null,
            median_total_response_latency_ms: null,
            median_total_response_latency_successful_ms: null,
            target_attainment: { speech_start_latency_ms: [0, 0] },
            median_longest_pause_ms: null,
            long_pauses_per_speaking_min: null,
            aggregate_silence_ratio: null,
          }),
        }),
      ],
    })
    renderPage()
    await screen.findByRole('heading', { name: 'Speed and fluency' })
    for (const label of [
      'Preparation used (median)',
      'Start of speech (median)',
      'Total time before speaking (median)',
      'Targets met',
      'Longest pause (median)',
      'Long pauses per minute of speech',
      'Silence while speaking',
    ]) {
      expect(fact(label)).toHaveTextContent(/^unavailable$/)
    }
    expect(fact('Fluency (average)')).toHaveTextContent('unavailable (0 answers assessable)')
    expect(fact('Language')).toHaveTextContent('unavailable (0 answers assessable)')
  })

  it('lists the questions with bands and measurements, and links to each feedback', async () => {
    setupReportsWorld({ rounds: [roundReport()] })
    renderPage()
    const rows = (await screen.findAllByRole('listitem')).filter((li) => li.querySelector('.q-line'))
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('Jaki był cel sejsachtei wprowadzonej przez Solona?')
    expect(within(rows[0]).getByText('Very good')).toHaveClass('band', 'very-good')
    expect(rows[0]).toHaveTextContent('Factual 88')
    expect(rows[0]).toHaveTextContent('Prep. used 4 s')
    expect(rows[0]).toHaveTextContent('Start of speech 0.3 s')
    expect(rows[0]).toHaveTextContent('Fluency 81')
    expect(within(rows[0]).getByRole('link', { name: 'Feedback' })).toHaveAttribute('href', '/rounds/5/answers/10')
    expect(within(rows[2]).getByText('Restarted after a pause')).toHaveClass('chip')
    expect(within(rows[3]).getByText("I don't know")).toHaveClass('chip')
    expect(rows[3]).toHaveTextContent('Factual 0')
    expect(rows[3]).toHaveTextContent('Prep. used —')
    expect(rows[3]).toHaveTextContent('Start of speech —')
    expect(rows[3]).toHaveTextContent('Fluency —')
    expect(within(rows[3]).getByRole('link', { name: 'Feedback' })).toHaveAttribute('href', '/rounds/5/answers/13')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(0)
  })

  it('names the interruptions', async () => {
    setupReportsWorld({ rounds: [roundReport()] })
    renderPage()
    const box = (await screen.findByRole('heading', { name: 'Interruptions' })).parentElement as HTMLElement
    expect(box).toHaveTextContent('1 pause between questions (4:00 in total) · 1 question restarted after a pause (question 3)')
    expect(box).toHaveTextContent('Interrupted attempts are left out of speed trends.')
  })

  it('has no interruptions box without interruptions', async () => {
    setupReportsWorld({ rounds: [roundReport({ pause_count: 0, paused_ms: 0, restarted_ordinals: [] })] })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByRole('heading', { name: 'Interruptions' })).not.toBeInTheDocument()
  })

  it('puts Polish content in lang="pl-PL" nodes without breakable one-letter words', async () => {
    setupReportsWorld({ rounds: [roundReport()] })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const polish = [...document.querySelectorAll('[lang="pl-PL"]')]
    expect(polish.length).toBeGreaterThanOrEqual(4)
    for (const node of polish) expect(node.textContent ?? '').not.toMatch(BREAKABLE)
    expect(screen.getByText(/Omów reformy Klejstenesa/).closest('.q-line')).toHaveAttribute('lang', 'pl-PL')
  })
})

describe('SummaryPage: provisional results', () => {
  it('warns with the counts and marks both panels provisional', async () => {
    setupReportsWorld({ rounds: [provisionalRoundReport()] })
    renderPage()
    const notice = (await screen.findByText('Provisional results.')).closest('.notice') as HTMLElement
    expect(notice).toHaveClass('warn')
    expect(notice).toHaveTextContent('3 answers are assessed; 1 could not be processed yet.')
    expect(notice).toHaveTextContent('Scores below cover the assessed answers only and may change.')
    expect(screen.getAllByText('Provisional')).toHaveLength(2)
    for (const chip of screen.getAllByText('Provisional')) expect(chip).toHaveClass('chip', 'warn')
    expect(document.querySelector('.headline b')).toHaveTextContent('61')
    expect(screen.getByText('3 of 4 answers assessed.')).toBeInTheDocument()
    expect(screen.getByText('0 Weak')).toBeInTheDocument()
  })

  it('shows an unresolved answer without a score and offers one primary "Try again"', async () => {
    setupReportsWorld({ rounds: [provisionalRoundReport()] })
    renderPage()
    const row = (await screen.findByText('Unresolved')).closest('li') as HTMLElement
    expect(screen.getByText('Unresolved')).toHaveClass('chip', 'error')
    expect(row).toHaveTextContent('The transcription service did not respond.')
    expect(row.querySelector('.band')).toBeNull()
    expect(within(row).queryByRole('link', { name: 'Feedback' })).not.toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Try again' })).toHaveClass('btn', 'small', 'primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('only the first of several retryable answers is primary', async () => {
    const base = provisionalRoundReport()
    const second = { ...base.items[2], ordinal: 4, item_id: 54, attempt_id: 14 }
    setupReportsWorld({ rounds: [{ ...base, items: [...base.items.slice(0, 3), second] }] })
    renderPage()
    const buttons = await screen.findAllByRole('button', { name: 'Try again' })
    expect(buttons[0]).toHaveClass('primary')
    expect(buttons[1]).not.toHaveClass('primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('shows no "Try again" for a failure the server says cannot be retried', async () => {
    const base = provisionalRoundReport()
    const failed = { ...base.items[2], failure: { kind: 'audio_missing', message: 'The recording never reached the app.', retryable: false } }
    setupReportsWorld({ rounds: [{ ...base, items: [base.items[0], base.items[1], failed, base.items[3]] }] })
    renderPage()
    expect(await screen.findByText('The recording never reached the app.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByText('Unresolved')).toBeInTheDocument()
  })

  it('"Try again" posts the retry and keeps reading until the answer is resolved', async () => {
    const base = provisionalRoundReport()
    const processing: RoundReport = {
      ...base,
      items: base.items.map((row) =>
        row.status === 'failed' ? { ...row, status: 'processing' as const, failure: null, outcome: 'answered' } : row,
      ),
    }
    // the page asks once for recovery and reads the report again (the second base)
    const world = setupReportsWorld({ rounds: [base, base, processing, processing, processing, roundReport()], retry: true })
    renderPage(150)
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(world.retries).toEqual([12]))
    expect(await screen.findByText('Still being processed')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Provisional results.')).not.toBeInTheDocument(), { timeout: 3000 })
    expect(screen.queryByText('Unresolved')).not.toBeInTheDocument()
    expect(screen.queryByText('Still being processed')).not.toBeInTheDocument()
    const calls = world.roundCalls
    await act(() => new Promise((resolve) => setTimeout(resolve, 500)))
    expect(world.roundCalls).toBe(calls)
  })

  it('says so when the retry cannot be sent', async () => {
    setupReportsWorld({ rounds: [provisionalRoundReport()] })
    server.use(http.post('/api/attempts/:id/retry', () => HttpResponse.json({ detail: 'This attempt cannot be retried.' }, { status: 409 })))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('This attempt cannot be retried.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
  })

  it('stops reading after five minutes of processing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const base = provisionalRoundReport()
    const processing: RoundReport = {
      ...base,
      items: base.items.map((row) => (row.status === 'failed' ? { ...row, status: 'processing' as const, failure: null } : row)),
    }
    const world = setupReportsWorld({ rounds: [processing] })
    renderPage(60_000)
    await screen.findByText('Provisional results.')
    for (let i = 0; i < 6; i += 1) await act(() => vi.advanceTimersByTimeAsync(60_000))
    const calls = world.roundCalls
    await act(() => vi.advanceTimersByTimeAsync(180_000))
    expect(world.roundCalls).toBe(calls)
    expect(calls).toBeLessThanOrEqual(7)
  })
})

describe('SummaryPage: ended early and still running', () => {
  it('says the round ended early and counts the questions not asked', async () => {
    setupReportsWorld({ rounds: [endedEarly()] })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Round ended early' })).toBeInTheDocument()
    expect((screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement).textContent).toContain('1 of 3 questions asked')
    expect(screen.getByText('1 of 1 answers assessed · 2 questions not asked (not counted).')).toBeInTheDocument()
    expect(screen.getAllByText('Not asked')).toHaveLength(2)
    expect(screen.getByText(/The round ended early\. Scores below cover the questions that were asked\./)).toBeInTheDocument()
    expect(screen.getByText(/An ended-early round has no retry round or extension/)).toBeInTheDocument()
    expect(screen.getAllByText('Provisional')).toHaveLength(2)
  })

  it('shows only a notice while an exam round is running', async () => {
    setupReportsWorld({
      rounds: [
        roundReport({
          status: 'active',
          finished_at: null,
          withheld: true,
          knowledge: null,
          delivery: null,
          items: [item({ band: null, factual: null, total: null, fluency: null, preparation_used_ms: null, speech_start_latency_ms: null })],
          config: { ...roundReport().config, feedback_timing: 'exam' },
        }),
      ],
    })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Round in progress' })).toBeInTheDocument()
    expect(screen.getByText(/Results appear when the round ends\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the round' })).toHaveAttribute('href', '/rounds/5')
    expect(document.querySelector('.band')).toBeNull()
    expect(document.querySelector('.panel')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Questions' })).not.toBeInTheDocument()
  })

  it('does not keep reading a running exam round, even with an answer still processing', async () => {
    const world = setupReportsWorld({
      rounds: [
        roundReport({
          status: 'active',
          finished_at: null,
          withheld: true,
          knowledge: null,
          delivery: null,
          items: [item({ status: 'processing', band: null, factual: null, total: null, fluency: null })],
          config: { ...roundReport().config, feedback_timing: 'exam' },
        }),
      ],
    })
    renderPage(20)
    expect(await screen.findByRole('heading', { level: 1, name: 'Round in progress' })).toBeInTheDocument()
    await act(() => new Promise((resolve) => setTimeout(resolve, 200)))
    expect(world.roundCalls).toBe(1)
  })

  it('says so when the round cannot be loaded', async () => {
    setupReportsWorld()
    server.use(http.get('/api/reports/rounds/:id', () => HttpResponse.json({ detail: 'Round not found.' }, { status: 404 })))
    renderPage()
    expect(await screen.findByText('Round not found.')).toBeInTheDocument()
  })
})

describe('SummaryPage: follow-ups', () => {
  const box = () => screen.getByRole('heading', { level: 2, name: 'Follow-ups' }).closest('section') as HTMLElement

  it('shows the follow-ups apart; the headline is the main score', async () => {
    const report = roundReportWithFollowUps({ knowledge: knowledge({ primary_score: 54 }) })
    setupReportsWorld({ rounds: [report] })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Follow-ups' })
    expect(box()).toHaveClass('box', 'tint', 'flow')
    expect(within(box()).getByText('1 asked · average factual 89 · 1 Good or better')).toBeInTheDocument()
    expect(within(box()).getByText('Scored separately; not in the round score.')).toHaveClass('small', 'muted')
    expect(within(box()).queryByText('Provisional')).toBeNull()
    expect(document.querySelector('.headline b')).toHaveTextContent('54')
  })

  it('counts the ones not asked and marks unresolved ones provisional', async () => {
    const series = followUpSeries({ asked: 2, not_asked: 1, unresolved: 1, provisional: true })
    setupReportsWorld({ rounds: [roundReportWithFollowUps({ follow_ups: series })] })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Follow-ups' })
    expect(within(box()).getByText(/2 asked · average factual 89 · 1 Good or better · 1 not asked/)).toBeInTheDocument()
    expect(within(box()).getByText('Provisional')).toHaveClass('chip', 'warn')
  })

  it('keeps reading while a follow-up answer is still being processed', async () => {
    const pending = followUpSeries({ unresolved: 1, good_or_better: 0, provisional: true, processing: 1 })
    const world = setupReportsWorld({
      rounds: [...Array(4).fill(roundReportWithFollowUps({ follow_ups: pending })), roundReportWithFollowUps()],
    })
    renderPage(20)
    await screen.findByRole('heading', { level: 2, name: 'Follow-ups' })
    await waitFor(() => expect(within(box()).queryByText('Provisional')).toBeNull())
    expect(within(box()).getByText('1 asked · average factual 89 · 1 Good or better')).toBeInTheDocument()
    const calls = world.roundCalls
    expect(calls).toBeGreaterThanOrEqual(5)
    await act(() => new Promise((resolve) => setTimeout(resolve, 200)))
    expect(world.roundCalls).toBe(calls)
  })

  it('has no box when follow-ups were off', async () => {
    setupReportsWorld({ rounds: [roundReport({ follow_ups: null })] })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Knowledge and language' })
    expect(screen.queryByRole('heading', { level: 2, name: 'Follow-ups' })).toBeNull()
  })
})

describe('SummaryPage: source policy', () => {
  it('names Expand the topic in the head of an expanded round', async () => {
    const base = roundReport()
    setupReportsWorld({ rounds: [roundReport({ config: { ...base.config, source_policy: 'expanded' } })] })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Round finished' })).toBeInTheDocument()
    const head = (screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement).textContent as string
    expect(head).toMatch(/ · Practice feedback · My materials \+ expand · /)
    expect(head).not.toMatch(/Only my materials/)
  })
})

// ---- the adaptive round and its extension ----

const TOPICS = [
  { topic_id: 31, title: 'Reformy Klejstenesa' },
  { topic_id: 32, title: 'Ostracyzm' },
]

function offer(patch: Partial<ExtensionOffer> = {}): ExtensionOffer {
  return {
    round_id: 5,
    status: 'ready',
    requested: 3,
    available: 3,
    topics: TOPICS,
    job_id: null,
    extension_round_id: null,
    extension_status: null,
    needs_review: 0,
    ...patch,
  }
}

const withExtension = (patch: Partial<RoundReport> = {}) =>
  roundReport({ config: config({ question_count: 4, extension: { enabled: true, question_count: 3 } }), ...patch })

interface ExtensionWorld {
  offers: ExtensionOffer[]
  offerCalls: number
  starts: number
  generates: number
}

/** Offers in turn (the last one repeats), a start that creates round 9 and a generation job 77. */
function extensionApi(offers: ExtensionOffer[]): ExtensionWorld {
  const world: ExtensionWorld = { offers, offerCalls: 0, starts: 0, generates: 0 }
  server.use(
    http.get('/api/rounds/5/extension', () => {
      const next = world.offers[Math.min(world.offerCalls, world.offers.length - 1)]
      world.offerCalls += 1
      return HttpResponse.json(next)
    }),
    http.post('/api/rounds/5/extension', () => {
      world.starts += 1
      return HttpResponse.json({ id: 9 }, { status: 201 })
    }),
    http.post('/api/rounds/5/extension/generate', () => {
      world.generates += 1
      return HttpResponse.json({ job_id: 77 }, { status: 202 })
    }),
  )
  return world
}

function renderExtension(pollMs = 20, path = '/rounds/5/summary') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/rounds/:roundId/summary" element={<SummaryPage pollMs={pollMs} />} />
        <Route path="/practice/:roundId/microphone" element={<p>Microphone page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const keepPanel = () => screen.getByRole('heading', { level: 2, name: 'Keep practising' }).closest('section') as HTMLElement

describe('SummaryPage: keep practising with an adaptive extension', () => {
  it('offers the new questions on the weak topics; one primary starts them', async () => {
    setupReportsWorld({ rounds: [withExtension()] })
    const world = extensionApi([offer()])
    const user = userEvent.setup()
    renderExtension()
    const panel = (await screen.findByRole('heading', { level: 2, name: 'Keep practising' })).closest('section') as HTMLElement
    expect(panel).toHaveClass('panel')
    expect(within(panel).getByRole('heading', { level: 3, name: 'Adaptive extension' })).toBeInTheDocument()
    const text = within(panel).getByText(/new questions on this round's weak topics/)
    expect(text.textContent).toBe(
      "3 new questions on this round's weak topics (Reformy Klejstenesa, Ostracyzm). They are scored separately and don't change this round's score.",
    )
    const titles = text.querySelectorAll('span.content[lang="pl-PL"]')
    expect([...titles].map((node) => node.textContent)).toEqual(['Reformy Klejstenesa', 'Ostracyzm'])
    const primaries = document.querySelectorAll('.btn.primary')
    expect(primaries).toHaveLength(1)
    expect(primaries[0]).toHaveTextContent('Start 3 extra questions')
    await user.click(screen.getByRole('button', { name: 'Start 3 extra questions' }))
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    expect(world.starts).toBe(1)
  })

  it('a short bank offers fewer questions and generating more first', async () => {
    setupReportsWorld({ rounds: [withExtension()] })
    extensionApi([offer({ available: 1 })])
    renderExtension()
    expect(await screen.findByRole('button', { name: 'Start 1 extra question' })).toHaveClass('btn', 'primary')
    expect(screen.getByText('Only 1 new question is in the bank for these topics.')).toHaveClass('note')
    const more = screen.getByRole('button', { name: 'Generate more questions first' })
    expect(more).toHaveClass('btn')
    expect(more).not.toHaveClass('primary')
  })

  it('with no new questions, generating them shows the progress until they are ready', async () => {
    setupReportsWorld({ rounds: [withExtension()] })
    const world = extensionApi([offer({ status: 'no_questions', available: 0 })])
    const user = userEvent.setup()
    renderExtension()
    const generate = await screen.findByRole('button', { name: 'Generate new questions' })
    expect(generate).toHaveClass('btn', 'primary')
    world.offers.push(offer({ status: 'generating', available: 0, job_id: 77 }), offer({ available: 3 }))
    world.offerCalls = 1
    await user.click(generate)
    expect(world.generates).toBe(1)
    expect(await screen.findByRole('status')).toHaveTextContent('Preparing new questions…')
    expect(await screen.findByRole('button', { name: 'Start 3 extra questions' })).toBeInTheDocument()
    expect(screen.queryByText('Preparing new questions…')).toBeNull()
  })

  it('stops reading after ten minutes of generation and offers a check', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setupReportsWorld({ rounds: [withExtension()] })
    const world = extensionApi([offer({ status: 'generating', available: 0, job_id: 77 })])
    renderExtension(3000)
    expect(await screen.findByText('Preparing new questions…')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 3000)
    })
    expect(await screen.findByText('The new questions are taking longer than usual.')).toBeInTheDocument()
    const reads = world.offerCalls
    const check = screen.getByRole('button', { name: 'Check again' })
    expect(check).toHaveClass('btn', 'small')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(world.offerCalls).toBe(reads)
    world.offers.push(offer())
    world.offerCalls = world.offers.length - 1
    await act(async () => {
      check.click()
    })
    expect(await screen.findByRole('button', { name: 'Start 3 extra questions' })).toBeInTheDocument()
  })

  it('names questions waiting for the bank review', async () => {
    setupReportsWorld({ rounds: [withExtension()] })
    extensionApi([offer({ status: 'no_questions', available: 0, needs_review: 2 })])
    renderExtension()
    const note = await screen.findByText(/New questions may be waiting for review/)
    expect(within(note).getByRole('link', { name: 'Open the question bank' })).toHaveAttribute('href', '/library/1/questions')
  })

  it('waits for every answer to be assessed, reading the offer with the report', async () => {
    setupReportsWorld({ rounds: [withExtension()] })
    const world = extensionApi([offer({ status: 'waiting', available: 0, topics: [] })])
    renderExtension()
    expect(await screen.findByText('The extension opens once every answer is assessed.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
    await waitFor(() => expect(world.offerCalls).toBeGreaterThanOrEqual(3))
    world.offers = [offer()]
    expect(await screen.findByRole('button', { name: 'Start 3 extra questions' })).toBeInTheDocument()
  })

  it('says so when no answer fell below Good', async () => {
    setupReportsWorld({ rounds: [withExtension()] })
    extensionApi([offer({ status: 'no_weak', available: 0, topics: [] })])
    renderExtension()
    expect(await screen.findByText('No answer fell below Good, so there is no extension this time.')).toBeInTheDocument()
    expect(within(keepPanel()).queryByRole('button')).toBeNull()
  })

  it.each(['ended_early', 'off', 'not_finished'] as const)('shows no panel for %s', async (status) => {
    setupReportsWorld({ rounds: [withExtension()] })
    const world = extensionApi([offer({ status, available: 0, topics: [] })])
    renderExtension()
    await screen.findByRole('heading', { level: 1 })
    await waitFor(() => expect(world.offerCalls).toBeGreaterThan(0))
    expect(screen.queryByText('Keep practising')).toBeNull()
  })

  it('does not ask for an offer when the option was off', async () => {
    setupReportsWorld({ rounds: [roundReport()] })
    const world = extensionApi([offer()])
    renderExtension()
    await screen.findByRole('heading', { level: 1, name: 'Round finished' })
    expect(world.offerCalls).toBe(0)
    expect(screen.queryByText('Keep practising')).toBeNull()
  })

  it('a retry button keeps the one primary; the extension action is then plain', async () => {
    setupReportsWorld({ rounds: [{ ...provisionalRoundReport(), config: withExtension().config }] })
    extensionApi([offer()])
    renderExtension()
    const start = await screen.findByRole('button', { name: 'Start 3 extra questions' })
    expect(start).toHaveClass('btn')
    expect(start).not.toHaveClass('primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveClass('primary')
  })
})

const SERIES = {
  round_id: 9,
  status: 'finished',
  asked: 1,
  total: 1,
  good_or_better: 0,
  provisional: false,
  withheld: false,
  knowledge: knowledge({ primary_score: 0, mean_factual: 0 }),
}

describe('SummaryPage: a started extension', () => {
  it('continues an open extension', async () => {
    setupReportsWorld({ rounds: [withExtension({ extension: { ...SERIES, status: 'active' } })] })
    extensionApi([offer({ status: 'started', extension_round_id: 9, extension_status: 'active' })])
    renderExtension()
    expect(await screen.findByRole('link', { name: 'Continue the extension' })).toHaveAttribute('href', '/rounds/9')
  })

  it('links the finished extension and shows its own series apart from the score', async () => {
    setupReportsWorld({ rounds: [withExtension({ extension: SERIES })] })
    extensionApi([offer({ status: 'started', extension_round_id: 9, extension_status: 'finished' })])
    renderExtension()
    const link = await screen.findByRole('link', { name: 'Extension summary' })
    expect(link).toHaveAttribute('href', '/rounds/9/summary')
    const box = screen.getByRole('heading', { level: 2, name: 'Adaptive extension' }).closest('section') as HTMLElement
    expect(box).toHaveClass('box', 'tint', 'flow')
    expect(box).toHaveAttribute('aria-labelledby', 'h-extension')
    expect(within(box).getByText('1 asked · average factual 0 · 0 Good or better')).toBeInTheDocument()
    expect(within(box).getByText('Scored separately; not in the round score.')).toHaveClass('small', 'muted')
    expect(document.querySelector('.headline b')).toHaveTextContent('64')
  })

  it('the extension summary has its own title and leads back to the round', async () => {
    setupReportsWorld({
      rounds: [
        roundReport({
          round_id: 9,
          parent_round_id: 5,
          config: config({ round_type: 'extension', question_count: 1, extension: { enabled: false, question_count: 3 } }),
        }),
      ],
    })
    const world = extensionApi([offer()])
    renderExtension()
    expect(await screen.findByRole('heading', { level: 1, name: 'Adaptive extension finished' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the round' })).toHaveAttribute('href', '/rounds/5/summary')
    expect(screen.queryByText('Keep practising')).toBeNull()
    expect(world.offerCalls).toBe(0)
    const head = (screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement).textContent as string
    expect(head).toContain('· Adaptive extension ·')
  })
})

describe('SummaryPage: an adaptive round', () => {
  it('says how the questions were chosen', async () => {
    setupReportsWorld({
      rounds: [roundReport({ config: config({ round_type: 'adaptive' }), selection_counts: { weak: 2, unpractised: 1, review: 1 } })],
    })
    renderExtension()
    expect(await screen.findByText('Chosen from your coverage: 2 Needs work · 1 Not practised · 1 review')).toHaveClass('note')
    const head = (screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement).textContent as string
    expect(head).toContain('· Adaptive ·')
  })

  it('a subject without answers was spread evenly', async () => {
    setupReportsWorld({ rounds: [roundReport({ config: config({ round_type: 'adaptive' }), selection_counts: { balanced: 4 } })] })
    renderExtension()
    expect(
      await screen.findByText('No answers on this subject yet, so the questions are spread evenly over the topics.'),
    ).toHaveClass('note')
  })
})

// ---- answer again and the retry round ----

function retryOffer(patch: Partial<RetryOffer> = {}): RetryOffer {
  return {
    round_id: 5,
    status: 'ready',
    count: 4,
    ordinals: [1, 2, 3, 4],
    unresolved: 0,
    retry_round_id: null,
    retry_status: null,
    ...patch,
  }
}

interface RetryWorld {
  offers: RetryOffer[]
  offerCalls: number
  starts: number
}

function retryApi(offers: RetryOffer[]): RetryWorld {
  const world: RetryWorld = { offers, offerCalls: 0, starts: 0 }
  server.use(
    http.get('/api/rounds/:id/retry', () => {
      const next = world.offers[Math.min(world.offerCalls, world.offers.length - 1)]
      world.offerCalls += 1
      return HttpResponse.json(next)
    }),
    http.post('/api/rounds/5/retry', () => {
      world.starts += 1
      return HttpResponse.json({ id: 12 }, { status: 201 })
    }),
  )
  return world
}

const withRetry = (patch: Partial<RoundReport> = {}, extension = false) =>
  roundReport({
    config: config({ question_count: 4, retry_round: true, immediate_repeat: true, extension: { enabled: extension, question_count: 3 } }),
    ...patch,
  })

const retryColumn = () => within(keepPanel()).getByRole('heading', { level: 3, name: 'Retry round' }).parentElement as HTMLElement

describe('SummaryPage: keep practising with a retry round', () => {
  it('offers the questions below Good; its start is the one primary and opens the microphone check', async () => {
    setupReportsWorld({ rounds: [withRetry()] })
    const world = retryApi([retryOffer()])
    const user = userEvent.setup()
    renderExtension()
    await screen.findByRole('heading', { level: 3, name: 'Retry round' })
    expect(keepPanel()).toHaveClass('panel')
    expect(
      within(retryColumn()).getByText('The 4 questions below Good, asked again with the same time limits. Your original scores stay.'),
    ).toBeInTheDocument()
    const start = within(retryColumn()).getByRole('button', { name: 'Start retry round (4)' })
    expect(start).toHaveClass('btn', 'primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    await user.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    expect(world.starts).toBe(1)
  })

  it('next to the extension, the extension keeps the primary and the retry start is plain', async () => {
    setupReportsWorld({ rounds: [withRetry({}, true)] })
    extensionApi([offer()])
    retryApi([retryOffer()])
    renderExtension()
    const start = await screen.findByRole('button', { name: 'Start retry round (4)' })
    expect(start).not.toHaveClass('primary')
    expect(screen.getByRole('button', { name: 'Start 3 extra questions' })).toHaveClass('primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(within(keepPanel()).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
      'Adaptive extension',
      'Retry round',
    ])
  })

  it('with a failed retryable answer, "Try again" is the primary', async () => {
    setupReportsWorld({ rounds: [{ ...provisionalRoundReport(), config: withRetry().config }] })
    retryApi([retryOffer({ count: 1, ordinals: [2], unresolved: 1 })])
    renderExtension()
    const start = await screen.findByRole('button', { name: 'Start retry round (1)' })
    expect(start).not.toHaveClass('primary')
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveClass('primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(
      within(retryColumn()).getByText('1 answer could not be assessed and is not in the retry round; try it again in the list below.'),
    ).toHaveClass('note')
  })

  it.each([
    ['waiting', 'The retry round opens once every answer is assessed.'],
    ['extension_open', 'Finish the adaptive extension first; the retry round comes after it.'],
    ['none_below', 'Every answer reached Good or better, so there is no retry round this time.'],
  ] as const)('%s: says why there is no start', async (status, text) => {
    setupReportsWorld({ rounds: [withRetry()] })
    retryApi([retryOffer({ status, count: 0, ordinals: [] })])
    renderExtension()
    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Start retry round/ })).toBeNull()
  })

  it('a started retry round is continued, then summarised', async () => {
    setupReportsWorld({ rounds: [withRetry()] })
    retryApi([retryOffer({ status: 'started', retry_round_id: 12, retry_status: 'active' })])
    const { unmount } = renderExtension()
    expect(await screen.findByRole('link', { name: 'Continue the retry round' })).toHaveAttribute('href', '/rounds/12')
    unmount()
    retryApi([retryOffer({ status: 'started', retry_round_id: 12, retry_status: 'finished' })])
    renderExtension()
    expect(await screen.findByRole('link', { name: 'Retry round summary' })).toHaveAttribute('href', '/rounds/12/summary')
  })

  it.each(['off', 'ended_early', 'not_finished'] as const)('%s: no column and no panel', async (status) => {
    setupReportsWorld({ rounds: [withRetry()] })
    const world = retryApi([retryOffer({ status })])
    renderExtension()
    await screen.findByRole('heading', { level: 1 })
    await waitFor(() => expect(world.offerCalls).toBeGreaterThan(0))
    expect(screen.queryByText('Keep practising')).toBeNull()
  })

  it('does not ask for an offer when the option was off', async () => {
    setupReportsWorld({ rounds: [roundReport()] })
    const world = retryApi([retryOffer()])
    renderExtension()
    await screen.findByRole('heading', { level: 1, name: 'Round finished' })
    expect(world.offerCalls).toBe(0)
  })
})

const REPEAT_BRIEF = {
  attempt_id: 20,
  outcome: 'answered',
  status: 'ready' as const,
  band: 'good' as const,
  factual: 70,
  total: 74,
  preparation_used_ms: 5000,
  long_pause_count: 1,
  fluency: 70,
}

describe('SummaryPage: answered again and the retry box', () => {
  function repeated(): RoundReport {
    const base = withRetry()
    const items = base.items.map((row, i) => (i === 1 ? { ...row, factual: 35, again: REPEAT_BRIEF } : row))
    return {
      ...base,
      items,
      repeats: {
        asked: 1,
        good_or_better: 1,
        improved: 1,
        rows: [
          {
            ordinal: 2,
            item_id: 51,
            question_text: items[1].question_text,
            original: { ...REPEAT_BRIEF, attempt_id: 11, band: 'needs_improvement', factual: 35 },
            again: REPEAT_BRIEF,
            factual_change: 35,
            below_good: false,
          },
        ],
        knowledge: knowledge({ primary_score: 74, mean_factual: 70 }),
        delivery: null,
      },
      retry: {
        round_id: 12,
        status: 'finished',
        started_at: '2026-10-22T16:44:00Z',
        asked: 4,
        total: 4,
        good_or_better: 2,
        still_below: 2,
        provisional: false,
        withheld: false,
        knowledge: knowledge({ primary_score: 58 }),
      },
    }
  }

  it('marks an answered-again row with its band and "With help"', async () => {
    setupReportsWorld({ rounds: [repeated()] })
    retryApi([retryOffer({ status: 'started', retry_round_id: 12, retry_status: 'finished' })])
    renderExtension()
    const row = (await screen.findByText('Answered again:')).closest('.meta') as HTMLElement
    expect(within(row).getByText('Good')).toHaveClass('band', 'good')
    expect(within(row).getByText('With help')).toHaveClass('chip')
    expect(screen.getAllByText('Answered again:')).toHaveLength(1)
  })

  it('shows the repeats and the retry round in boxes; the knowledge headline is unchanged', async () => {
    setupReportsWorld({ rounds: [repeated()] })
    retryApi([retryOffer({ status: 'started', retry_round_id: 12, retry_status: 'finished' })])
    renderExtension()
    const again = (await screen.findByRole('heading', { level: 2, name: 'Answer again' })).closest('section') as HTMLElement
    expect(again).toHaveClass('box', 'tint', 'flow')
    expect(within(again).getByText('1 answered again · average factual 35 → 70 · 1 Good or better')).toBeInTheDocument()
    expect(within(again).getByText('With help; not in the round score.')).toHaveClass('small', 'muted')

    const box = screen.getByRole('heading', { level: 2, name: 'Retry round' }).closest('section') as HTMLElement
    expect(box).toHaveAttribute('aria-labelledby', 'h-retry')
    const link = within(box).getByRole('link')
    expect(link).toHaveAttribute('href', '/rounds/12/summary')
    expect(link).toHaveClass('title', 'num')
    const meta = within(box).getByText('4 questions').closest('.meta') as HTMLElement
    expect([...meta.querySelectorAll('span')].map((s) => s.textContent)).toEqual(['4 questions', 'Knowledge and language 58'])
    expect(within(box).getByText("The retry round keeps its own score. This round's 64 does not change.")).toBeInTheDocument()
    expect(document.querySelector('.headline b')).toHaveTextContent('64')
  })
})

function retryRoundReport(): RoundReport {
  const rows = [
    { ordinal: 1, item_id: 60, before: ['needs_improvement', 44], after: ['good', 71], change: 27 },
    { ordinal: 2, item_id: 61, before: ['needs_improvement', 51], after: ['very_good', 84], change: 33 },
    { ordinal: 3, item_id: 62, before: ['weak', 0, 'dont_know'], after: ['needs_improvement', 38], change: 38 },
    { ordinal: 4, item_id: 63, before: ['weak', 22], after: ['weak', 25], change: 3 },
  ] as const
  const texts = ['Pytanie pierwsze?', 'Pytanie drugie?', 'Kim byli metojkowie i jakie mieli prawa?', 'Jaką rolę odgrywała mistoforia?']
  const brief = (band: string, factual: number, outcome = 'answered') => ({
    attempt_id: 1,
    outcome,
    status: 'ready' as const,
    band: band as 'weak',
    factual,
    total: factual,
    preparation_used_ms: 9000,
    long_pause_count: 0,
    fluency: null,
  })
  return roundReport({
    round_id: 12,
    subject_id: 3,
    parent_round_id: 5,
    config: config({ round_type: 'retry', question_count: 4, retry_round: false, immediate_repeat: false }),
    knowledge: knowledge({ primary_score: 58 }),
    items: rows.map((row, i) => item({ ordinal: row.ordinal, item_id: row.item_id, attempt_id: 30 + i, question_text: texts[i] })),
    retry_comparison: {
      parent_round_id: 5,
      parent_started_at: '2026-10-22T16:05:00Z',
      paired: 4,
      original_mean_total: 31,
      retry_mean_total: 58,
      original_mean_factual: 29,
      retry_mean_factual: 55,
      original_median_preparation_ms: 15_000,
      retry_median_preparation_ms: 9000,
      rows: rows.map((row, i) => ({
        ordinal: row.ordinal,
        item_id: row.item_id,
        question_text: texts[i],
        original: brief(row.before[0], row.before[1], row.before.length > 2 ? 'dont_know' : 'answered'),
        again: brief(row.after[0], row.after[1]),
        factual_change: row.change,
        below_good: i >= 2,
      })),
      still_below: [3, 4],
    },
  })
}

describe('SummaryPage: the retry round summary', () => {
  it('has its own title, meta and the link back as "Original round"', async () => {
    setupReportsWorld({ rounds: [retryRoundReport()] })
    const world = retryApi([retryOffer()])
    renderExtension(20, '/rounds/12/summary')
    expect(await screen.findByRole('heading', { level: 1, name: 'Retry round finished' })).toBeInTheDocument()
    const meta = (screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement).querySelector('p:not(.crumbs)') as HTMLElement
    expect(meta).toHaveTextContent(
      /^Retry of the round from .+ · Demokracja ateńska · 4 questions that were below Good · same questions and time limits · My materials$/,
    )
    expect(within(meta).getByText('Demokracja ateńska')).not.toHaveAttribute('lang')
    const back = screen.getByRole('link', { name: 'Original round' })
    expect(back).toHaveAttribute('href', '/rounds/5/summary')
    expect(back).toHaveClass('btn')
    expect(screen.queryByRole('link', { name: 'Back to the round' })).toBeNull()
    expect(world.offerCalls).toBe(0)
    expect(screen.queryByText('Keep practising')).toBeNull()
  })

  it('shows the scores, original and retry per question, still below Good and speed', async () => {
    setupReportsWorld({ rounds: [retryRoundReport()] })
    renderExtension(20, '/rounds/12/summary')
    const score = (await screen.findByRole('heading', { level: 2, name: 'Retry round score' })).closest('section') as HTMLElement
    const cells = [...score.querySelectorAll('.scores > div')].map((cell) => [
      cell.querySelector('.k')?.textContent,
      cell.querySelector('.v')?.textContent,
      cell.querySelector('.note')?.textContent,
    ])
    expect(cells).toEqual([
      ['Knowledge and language, this retry', '58', 'Average of the 4 retry answers'],
      ['Same questions, original round', '31', 'Unchanged; the original round keeps its score'],
      ['Improvement', '+27', 'Factual 29 → 55 (+26)'],
    ])

    const compare = screen.getByRole('heading', { level: 2, name: 'Original and retry' }).closest('section') as HTMLElement
    const first = within(compare).getAllByRole('listitem')[0]
    expect(within(first).getByText('+27')).toBeInTheDocument()
    expect(first).toHaveTextContent('+27 factual')
    const third = within(compare).getAllByRole('listitem')[2]
    expect(third.querySelector('.meta')).toHaveTextContent('OriginalWeakI don’t know→ RetryNeeds improvement38')

    const still = screen.getByRole('heading', { level: 2, name: 'Still below Good' }).closest('section') as HTMLElement
    const list = within(still).getByRole('list')
    expect(list).toHaveClass('list')
    expect(list).toHaveAttribute('lang', 'pl-PL')
    // plNbsp keeps one-letter words with the next word
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent?.replace(/\u00a0/g, ' '))).toEqual([
      'Kim byli metojkowie i jakie mieli prawa?',
      'Jaką rolę odgrywała mistoforia?',
    ])
    const fresh = within(still).getByRole('link', { name: 'New round on these topics' })
    expect(fresh).toHaveAttribute('href', '/practice?subject=3&sources=materials')
    expect(fresh).toHaveClass('btn', 'primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)

    const speed = screen.getByRole('heading', { level: 2, name: 'Speed in the retry' }).closest('section') as HTMLElement
    expect(within(speed).getByText('Preparation used (median)').nextElementSibling).toHaveTextContent(
      '9 s down from 15 s in the original answers to these questions',
    )
    expect(screen.getByRole('heading', { level: 2, name: 'Questions' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 2, name: 'Knowledge and language' })).toBeNull()
  })

  it('an expanded round links its new round to the expanded questions', async () => {
    const report = retryRoundReport()
    report.config = { ...report.config, source_policy: 'expanded' }
    setupReportsWorld({ rounds: [report] })
    renderExtension(20, '/rounds/12/summary')
    expect(await screen.findByRole('link', { name: 'New round on these topics' })).toHaveAttribute(
      'href',
      '/practice?subject=3&sources=all',
    )
  })
})

describe('SummaryPage: recovery', () => {
  it('a provisional report with an unresolved answer asks the server to recover the round once, then reads it again', async () => {
    const world = setupReportsWorld({ rounds: [provisionalRoundReport(), roundReport()] })
    renderPage()
    await waitFor(() => expect(world.recoverCalls).toBe(1))
    await waitFor(() => expect(world.roundCalls).toBeGreaterThanOrEqual(2))
    expect(world.roundCallsAtRecover).toBe(1)
    await act(() => new Promise((resolve) => setTimeout(resolve, 100)))
    expect(world.recoverCalls).toBe(1)
  })

  it('a complete report needs no recovery', async () => {
    const world = setupReportsWorld({ rounds: [roundReport()] })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    await act(() => new Promise((resolve) => setTimeout(resolve, 100)))
    expect(world.recoverCalls).toBe(0)
  })
})

describe('SummaryPage: practice sources', () => {
  it('names an All questions round', async () => {
    const base = roundReport()
    setupReportsWorld({ rounds: [roundReport({ config: { ...base.config, source_policy: 'all' } })] })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Round finished' })).toBeInTheDocument()
    const head = (screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement).textContent as string
    expect(head).toMatch(/ · Practice feedback · All questions · /)
  })

  it('a retry of an All questions round links its new round to All questions', async () => {
    const report = retryRoundReport()
    report.config = { ...report.config, source_policy: 'all' }
    setupReportsWorld({ rounds: [report] })
    renderExtension(20, '/rounds/12/summary')
    expect(await screen.findByRole('link', { name: 'New round on these topics' })).toHaveAttribute(
      'href',
      '/practice?subject=3&sources=all',
    )
  })
})

describe('SummaryPage: the AI cost of the round', () => {
  it('lists "AI cost" with the round cost, and nothing without one', async () => {
    setupReportsWorld({ rounds: [roundReport({ ai_cost_usd: 0.48 })] })
    const view = renderPage()
    const term = await screen.findByText('AI cost')
    expect(term.tagName).toBe('DT')
    expect(term.closest('dl')).toHaveClass('facts')
    expect(term.nextElementSibling).toHaveTextContent('$0.48')
    view.unmount()
    setupReportsWorld({ rounds: [roundReport({ ai_cost_usd: null })] })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Round finished' })).toBeInTheDocument()
    expect(screen.queryByText('AI cost')).toBeNull()
  })
})
