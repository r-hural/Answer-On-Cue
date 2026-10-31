import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { progressStrings } from './strings'
import { TrendsPage } from './TrendsPage'
import { setupTrendsWorld, templatePoints, trendPoint, trendsOut } from './trends.mocks'

function Where() {
  const { pathname, search } = useLocation()
  return <p data-testid="where">{pathname + search}</p>
}

function renderPage(entry = '/progress/trends?subject=3') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Where />
      <Routes>
        <Route path="/progress/trends" element={<TrendsPage />} />
        <Route path="/progress/history" element={<p>History page</p>} />
        <Route path="/progress/coverage" element={<p>Coverage page</p>} />
        <Route path="/practice" element={<p>Practice page</p>} />
        <Route path="/library" element={<p>Library page</p>} />
        <Route path="/rounds/:id/summary" element={<p>Summary page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const where = () => screen.getByTestId('where').textContent
const section = async (name: string) => within(await screen.findByRole('region', { name }))
// The chart's svg: a labelled group, so its round points stay exposed as links.
const plot = { name: (_name: string, el: Element) => el.tagName.toLowerCase() === 'svg' }
const lastQuery = (world: { requests: string[] }) => world.requests.filter((r) => r.startsWith('/api/trends')).at(-1)

describe('TrendsPage layout', () => {
  it('shows the title, the tabs and the filters of the template', async () => {
    setupTrendsWorld()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Progress' })).toBeInTheDocument()
    const tabs = within(screen.getByRole('navigation', { name: 'Progress views' }))
    expect(tabs.getAllByRole('link').map((link) => link.textContent)).toEqual(['Coverage', 'History', 'Trends'])
    expect(tabs.getByRole('link', { name: 'Trends' })).toHaveAttribute('aria-current', 'page')

    const form = await screen.findByRole('form', { name: 'Show' })
    expect(form.querySelector('.filters.trend-filters')).not.toBeNull()
    const subject = screen.getByLabelText('Subject')
    expect(subject).toHaveClass('select', 'content')
    expect(subject).not.toHaveAttribute('lang')
    expect(subject).toHaveValue('3')
    const options = (label: string) =>
      within(screen.getByLabelText(label))
        .getAllByRole('option')
        .map((o) => o.textContent)
    expect(options('Sources')).toEqual(['My materials', 'All questions'])
    expect(options('Style')).toEqual(['All styles', 'Short factual', 'Open explanation'])
    expect(options('Difficulty')).toEqual([
      'All difficulties',
      'Basic recall',
      'Explanation and application',
      'Deeper analysis',
    ])
    expect(options('Questions')).toEqual([
      'Main questions',
      'Follow-ups',
      'Answer again',
      'Retry rounds',
      'Adaptive extensions',
    ])
    expect(screen.getByRole('checkbox', { name: 'Include interrupted attempts' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Include answers with help' })).not.toBeChecked()
    expect(screen.getByText('Off by default: trends show independent, uninterrupted answers.')).toBeInTheDocument()
  })

  it('has the four sections, each with a key and a caption, and no primary button', async () => {
    setupTrendsWorld()
    renderPage()
    for (const name of ['Knowledge and language', 'Speed, next to correctness', 'Pauses', 'Fluency']) {
      const region = await section(name)
      expect(await region.findByRole('heading', { level: 2, name })).toBeInTheDocument()
      expect(region.getByRole('list', { name: 'Key' })).toBeInTheDocument()
    }
    expect(document.querySelector('.trend-cap')).not.toBeNull()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(0)
    expect(document.querySelectorAll('svg.trend.wide')).toHaveLength(4)
  })

  it('shows the latest and the first values beside the charts', async () => {
    setupTrendsWorld()
    renderPage()
    const knowledge = await section('Knowledge and language')
    const scores = within(await knowledge.findByLabelText('Latest scores'))
    expect(scores.getByText('Knowledge and language, 22 Oct')).toBeInTheDocument()
    expect(scores.getByText('64')).toBeInTheDocument()
    expect(scores.getByText('48 on 12 Oct')).toBeInTheDocument()
    expect(scores.getByText('45 on 12 Oct')).toBeInTheDocument()
    expect(scores.getByText('10 answers assessable')).toBeInTheDocument()
    const speed = await section('Speed, next to correctness')
    expect(speed.getByText('Total time before speaking, 22 Oct').nextElementSibling).toHaveTextContent(
      '12 s 19 s on 12 Oct',
    )
    expect(speed.getByText('Start of speech', { selector: 'dt' }).nextElementSibling).toHaveTextContent('0.8 s 10 answers')
    const knowledgeChart = knowledge.getByRole('group', plot)
    expect(knowledgeChart).toHaveAttribute('aria-label', expect.stringContaining('Factual: 45, 49, 53, 57, 55, 60, 63'))
  })
})

describe('TrendsPage filters', () => {
  it('requests each filter, updates the URL and keeps the others', async () => {
    const world = setupTrendsWorld()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('form', { name: 'Show' })
    await user.selectOptions(screen.getByLabelText('Sources'), 'My materials')
    await waitFor(() => expect(lastQuery(world)).toBe('/api/trends?subject_id=3'))
    await user.selectOptions(screen.getByLabelText('Style'), 'Short factual')
    await user.selectOptions(screen.getByLabelText('Difficulty'), 'Deeper analysis')
    await user.selectOptions(screen.getByLabelText('Questions'), 'Follow-ups')
    await user.click(screen.getByRole('checkbox', { name: 'Include interrupted attempts' }))
    await waitFor(() =>
      expect(lastQuery(world)).toBe(
        '/api/trends?subject_id=3&series=followup&style=short&difficulty=analysis&include_interrupted=true',
      ),
    )
    expect(where()).toBe(
      '/progress/trends?subject=3&sources=materials&style=short&difficulty=analysis&questions=followup&interrupted=1',
    )
    await user.selectOptions(screen.getByLabelText('Questions'), 'Main questions')
    await user.selectOptions(screen.getByLabelText('Style'), 'All styles')
    await waitFor(() => expect(where()).toBe('/progress/trends?subject=3&sources=materials&difficulty=analysis&interrupted=1'))
    await user.selectOptions(screen.getByLabelText('Subject'), 'Biologia komórki')
    await waitFor(() =>
      expect(lastQuery(world)).toBe(
        '/api/trends?subject_id=4&difficulty=analysis&include_interrupted=true',
      ),
    )
  })

  it('preselects what the address carries', async () => {
    const world = setupTrendsWorld()
    renderPage('/progress/trends?subject=4&questions=followup&sources=expanded&style=open&assisted=1')
    expect(await screen.findByLabelText('Subject')).toHaveValue('4')
    expect(screen.getByLabelText('Questions')).toHaveValue('followup')
    expect(screen.getByLabelText('Sources')).toHaveValue('all')
    expect(screen.getByLabelText('Style')).toHaveValue('open')
    await waitFor(() =>
      expect(lastQuery(world)).toBe('/api/trends?subject_id=4&source_policy=all&series=followup&style=open'),
    )
  })

  it('opens the first subject without ?subject and writes it into the address', async () => {
    const world = setupTrendsWorld()
    renderPage('/progress/trends')
    await waitFor(() => expect(where()).toBe('/progress/trends?subject=3'))
    await waitFor(() => expect(lastQuery(world)).toBe('/api/trends?subject_id=3&source_policy=all'))
  })

  it('disables Include answers with help outside the main series and keeps its state', async () => {
    const world = setupTrendsWorld()
    const user = userEvent.setup()
    renderPage()
    const box = await screen.findByRole('checkbox', { name: 'Include answers with help' })
    await user.click(box)
    await waitFor(() => expect(lastQuery(world)).toBe('/api/trends?subject_id=3&source_policy=all&include_assisted=true'))
    await user.selectOptions(screen.getByLabelText('Questions'), 'Answer again')
    expect(box).toBeDisabled()
    expect(box).toBeChecked()
    expect(screen.getByText('Answer again and retry rounds are answers with help.')).toBeInTheDocument()
    await waitFor(() => expect(lastQuery(world)).toBe('/api/trends?subject_id=3&source_policy=all&series=immediate'))
    await user.selectOptions(screen.getByLabelText('Questions'), 'Main questions')
    expect(box).toBeEnabled()
    expect(box).toBeChecked()
    await waitFor(() => expect(lastQuery(world)).toBe('/api/trends?subject_id=3&source_policy=all&include_assisted=true'))
    expect(screen.queryByText('Answer again and retry rounds are answers with help.')).not.toBeInTheDocument()
  })
})

describe('TrendsPage values', () => {
  it('shows an unavailable start of speech as unavailable, never 0 s, and leaves a gap', async () => {
    const points = templatePoints()
    points[6] = { ...points[6], median_speech_start_latency_ms: null }
    setupTrendsWorld({ respond: () => trendsOut({ points }) })
    renderPage()
    const speed = await section('Speed, next to correctness')
    const term = await speed.findByText('Start of speech', { selector: 'dt' })
    expect(term.nextElementSibling).toHaveTextContent('unavailable')
    expect(term.nextElementSibling).not.toHaveTextContent('0 s')
    const label = speed.getByRole('group', plot).getAttribute('aria-label') ?? ''
    expect(label).toContain('Start of speech: 2.4, 2.1, 1.8, 1.5, 1.6, 1.2, unavailable seconds')
    // the dotted series ends at six values: one polyline of six points
    const dotted = speed.getByRole('group', plot).querySelectorAll('polyline.line.dot')
    expect(dotted).toHaveLength(1)
    expect(dotted[0].getAttribute('points')?.split(' ')).toHaveLength(6)
  })

  it('shows the filler rate as observed, only where the transcript is reliable', async () => {
    const points = templatePoints().map((p, i) =>
      trendPoint({ ...p, filler_per_min: i < 2 ? 2.1 : i === 6 ? 2.1 : null, filler_reliable: i < 2 || i === 6 ? 2 : 0 }),
    )
    setupTrendsWorld({ respond: () => trendsOut({ points: points.slice(0, 3).concat(points.slice(6)) }) })
    renderPage()
    const pauses = await section('Pauses')
    const term = await pauses.findByText('Filler rate', { selector: 'dt' })
    expect(term.nextElementSibling).toHaveTextContent('2.1 per minute')
    expect(term.nextElementSibling).toHaveTextContent('observed, a lower bound')
    expect(term.nextElementSibling).toHaveTextContent('Only where the transcript is reliable: 3 of 4 rounds')
    expect(pauses.getByText('Silence while speaking', { selector: 'dt' }).nextElementSibling).toHaveTextContent('16%')
  })

  it('writes unavailable for a filler rate and fluency that are all null', async () => {
    const points = templatePoints().map((p) =>
      trendPoint({ ...p, filler_per_min: null, filler_reliable: 0, fluency: null, fluency_assessable: 0 }),
    )
    setupTrendsWorld({ respond: () => trendsOut({ points }) })
    renderPage()
    const pauses = await section('Pauses')
    const term = await pauses.findByText('Filler rate', { selector: 'dt' })
    expect(term.nextElementSibling).toHaveTextContent('unavailable')
    expect(term.nextElementSibling).toHaveTextContent('0 of 7 rounds')
    const fluency = await section('Fluency')
    expect(fluency.getAllByText('unavailable').length).toBeGreaterThan(0)
    expect(fluency.getByRole('group', plot).querySelectorAll('polyline')).toHaveLength(0)
    expect(fluency.getByRole('group', plot).getAttribute('aria-label')).toContain('unavailable, unavailable')
  })

  it('names a missing value in the chart labels with the word from the strings', async () => {
    const strings = progressStrings.trends as { unavailable: string }
    const word = strings.unavailable
    strings.unavailable = 'n/a'
    try {
      const points = templatePoints().map((p) => trendPoint({ ...p, fluency: null, fluency_assessable: 0 }))
      setupTrendsWorld({ respond: () => trendsOut({ points }) })
      renderPage()
      const fluency = await section('Fluency')
      const label = fluency.getByRole('group', plot).getAttribute('aria-label') ?? ''
      expect(label).toContain('n/a, n/a')
      expect(label).not.toContain('unavailable')
    } finally {
      strings.unavailable = word
    }
  })
})

describe('TrendsPage captions', () => {
  it('names a round that ended early and rounds with help', async () => {
    const points = templatePoints()
    points[5] = { ...points[5], status: 'abandoned', answers: 4, provisional: true }
    points[6] = { ...points[6], assisted: 2 }
    setupTrendsWorld({ respond: () => trendsOut({ points }) })
    renderPage()
    const knowledge = await section('Knowledge and language')
    const caption = (await knowledge.findByText(/^One point per round, 7 rounds/)).textContent ?? ''
    expect(caption).toContain('21 Oct ended early after 4')
    expect(caption).toContain('(Needs improvement 30–59, Good 60–79)')
    expect(caption).toContain('Includes answers with help.')
    const titles = [...knowledge.getByRole('group', plot).querySelectorAll('title')].map((e) => e.textContent)
    expect(titles[5]).toContain('ended early')
  })

  it('marks a changed band table on the knowledge chart', async () => {
    const points = templatePoints()
    points[3] = { ...points[3], changes: ['band_table'] }
    setupTrendsWorld({ respond: () => trendsOut({ points }) })
    renderPage()
    const knowledge = await section('Knowledge and language')
    await knowledge.findByRole('group', plot)
    expect(knowledge.getByRole('group', plot).querySelector('text.mk')).toHaveTextContent('Band table changed')
    expect(knowledge.getByRole('group', plot).getAttribute('aria-label')).toContain('Band table changed before the round of 17 Oct')
    expect((await section('Speed, next to correctness')).getByRole('group', plot).querySelector('text.mk')).toBeNull()
  })

  it('calls out a sooner-but-less-factual round only when it is large enough', async () => {
    setupTrendsWorld()
    const { unmount } = renderPage()
    const speed = await section('Speed, next to correctness')
    await speed.findByRole('group', plot)
    expect(speed.getByRole('group', plot).querySelector('text.mk')).toBeNull()
    unmount()

    const points = templatePoints()
    points[4] = { ...points[4], factual: 50 }
    setupTrendsWorld({ respond: () => trendsOut({ points }) })
    renderPage()
    const again = await section('Speed, next to correctness')
    await waitFor(() => expect(again.getByRole('group', plot).querySelector('text.mk')).not.toBeNull())
    expect(again.getByRole('group', plot).querySelector('text.mk')).toHaveTextContent('Sooner, but factual fell')
    expect(again.getByText(/On 20 Oct you started sooner, but the factual score fell/)).toBeInTheDocument()
  })

  it('says restarted answers are left out of speed', async () => {
    const points = templatePoints()
    points[2] = { ...points[2], restarted: 1 }
    setupTrendsWorld({ respond: () => trendsOut({ points }) })
    renderPage()
    expect(await screen.findByText(/Answers restarted after an interruption are left out of speed/)).toBeInTheDocument()
  })
})

describe('TrendsPage without enough data', () => {
  const one = trendPoint({ round_id: 21, started_at: '2026-10-18T12:00:00Z', primary: 71 })

  it('draws the single round and offers a round, the round and clearing the filters', async () => {
    setupTrendsWorld({ respond: () => trendsOut({ subject_id: 4, subject_name: 'Biologia komórki', points: [one] }) })
    const user = userEvent.setup()
    renderPage('/progress/trends?subject=4&style=open')
    const heading = await screen.findByRole('heading', { level: 2, name: /No trends yet for/ })
    expect(heading).toHaveTextContent('No trends yet for „Biologia komórki”')
    expect(heading.querySelector('span.content')).not.toHaveAttribute('lang')
    expect(
      screen.getByText('Trends need at least 2 finished rounds with these filters. You have 1, so there is no line to draw yet.'),
    ).toBeInTheDocument()
    expect(document.querySelectorAll('svg.trend')).toHaveLength(1)
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Start a round' })).toHaveAttribute('href', '/practice?subject=4&sources=all')
    expect(screen.getByRole('link', { name: 'Start a round' })).toHaveClass('btn', 'primary')
    expect(screen.getByRole('link', { name: 'See the round of 18 Oct' })).toHaveAttribute('href', '/rounds/21/summary')
    await user.click(screen.getByRole('link', { name: 'Clear filters' }))
    await waitFor(() => expect(where()).toBe('/progress/trends?subject=4'))
  })

  it('offers no Clear filters on the defaults and carries expanded sources into Start a round', async () => {
    setupTrendsWorld({ respond: () => trendsOut({ points: [one] }) })
    renderPage('/progress/trends?subject=3')
    await screen.findByRole('heading', { level: 2, name: /No trends yet for/ })
    expect(screen.queryByRole('link', { name: 'Clear filters' })).not.toBeInTheDocument()
  })

  it('links Start a round with the expanded sources', async () => {
    setupTrendsWorld({ respond: () => trendsOut({ points: [one] }) })
    renderPage('/progress/trends?subject=3&sources=expanded')
    expect(await screen.findByRole('link', { name: 'Start a round' })).toHaveAttribute(
      'href',
      '/practice?subject=3&sources=all',
    )
  })

  it('says "none yet" and draws no chart without rounds', async () => {
    setupTrendsWorld({ respond: () => trendsOut({ points: [] }) })
    renderPage()
    expect(await screen.findByText(/You have none yet\./)).toBeInTheDocument()
    expect(document.querySelectorAll('svg.trend')).toHaveLength(0)
    expect(screen.queryByRole('link', { name: /See the round/ })).not.toBeInTheDocument()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('has an empty state without subjects, and no primary button', async () => {
    setupTrendsWorld({ subjects: [] })
    renderPage('/progress/trends')
    expect(await screen.findByRole('heading', { level: 2, name: 'No subjects yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to the Library' })).toHaveAttribute('href', '/library')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(0)
  })
})

describe('TrendsPage loading and failure', () => {
  it('says it is loading, then shows an error when the trends cannot be loaded', async () => {
    setupTrendsWorld({ respond: () => 500 })
    renderPage()
    expect(await screen.findByRole('status')).toHaveTextContent('Loading the trends…')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the trends.')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
