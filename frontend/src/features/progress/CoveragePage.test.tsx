import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { CoveragePage } from './CoveragePage'
import {
  emptySubjectCoverage,
  notPractised,
  objectiveCoverage,
  setupCoverageWorld,
  subjectCoverage,
  topicCoverage,
} from './coverage.mocks'

const BREAKABLE = /(^|[\s(„"«])[aiouwzAIOUWZ] /

function Where() {
  const { pathname, search } = useLocation()
  return <p data-testid="where">{pathname + search}</p>
}

function renderPage(entry = '/progress/coverage?subject=3') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Where />
      <Routes>
        <Route path="/progress/coverage" element={<CoveragePage />} />
        <Route path="/progress/topics/:topicId" element={<p>Topic page</p>} />
        <Route path="/progress/history" element={<p>History page</p>} />
        <Route path="/practice" element={<p>Practice page</p>} />
        <Route path="/library" element={<p>Library page</p>} />
        <Route path="/library/:id" element={<p>Subject page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const where = () => screen.getByTestId('where').textContent

describe('CoveragePage', () => {
  it('shows the title, the subject select, the tabs and the status key', async () => {
    setupCoverageWorld()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Progress' })).toBeInTheDocument()
    const select = await screen.findByLabelText('Subject')
    expect(select).toHaveClass('select', 'content')
    expect(select).not.toHaveAttribute('lang')
    expect(select).toHaveValue('3')
    const tabs = within(screen.getByRole('navigation', { name: 'Progress views' }))
    expect(tabs.getByRole('link', { name: 'Coverage' })).toHaveAttribute('aria-current', 'page')
    expect(tabs.getByRole('link', { name: 'Coverage' })).toHaveAttribute('href', '/progress/coverage?subject=3')
    const key = screen.getByLabelText('Status key')
    expect([...key.querySelectorAll('.status')].map((node) => node.textContent)).toEqual([
      'Not practised',
      'Needs work',
      'Improving',
      'Strong',
    ])
  })

  it('counts the learning objectives by status', async () => {
    setupCoverageWorld({
      coverage: {
        3: subjectCoverage({
          line_total: 37,
          line_counts: { strong: 6, improving: 11, needs_work: 12, not_practised: 8 },
        }),
      },
    })
    renderPage()
    await screen.findByLabelText('Learning objectives by status')
    const cells = [...document.querySelectorAll('.scores > div')] as HTMLElement[]
    expect(cells.map((cell) => cell.querySelector('.k')?.textContent)).toEqual([
      'Strong',
      'Improving',
      'Needs work',
      'Not practised',
    ])
    expect(cells.map((cell) => cell.querySelector('.v')?.textContent)).toEqual(['6', '11', '12', '8'])
    expect(cells.map((cell) => cell.querySelector('.of')?.textContent)).toEqual([
      'of 37 learning objectives',
      'of 37',
      'of 37',
      'of 37',
    ])
    expect(cells[0].querySelector('.status')).toHaveClass('strong')
    expect(cells[3].querySelector('.status')).toHaveClass('none')
  })

  it('lists the objectives by topic with status, answers, last date, scores and preparation', async () => {
    setupCoverageWorld()
    renderPage()
    await screen.findByRole('table')
    const table = document.querySelector('table.coverage-table') as HTMLElement
    const headers = [...table.querySelectorAll('thead th')].map((th) => th.textContent)
    expect(headers).toEqual(['Topic / learning objective', 'Status', 'Answers', 'Last', 'Recent factual', 'Delivery'])

    const groups = [...table.querySelectorAll('tbody th[colspan="6"]')]
    expect(groups).toHaveLength(2)
    const link = within(groups[0] as HTMLElement).getByRole('link')
    expect(link).toHaveClass('content-title', 'content')
    expect(link).toHaveAttribute('lang', 'pl-PL')
    expect(link).toHaveAttribute('href', '/progress/topics/7')
    expect(link).toHaveTextContent('Ostracyzm: procedura i cel')

    const rows = [...table.querySelectorAll('tbody tr')].filter((row) => row.querySelector('td.obj')) as HTMLElement[]
    expect(rows).toHaveLength(4)
    const first = rows[0]
    const objective = first.querySelector('td.obj') as HTMLElement
    expect(objective).toHaveClass('content')
    expect(objective).toHaveAttribute('lang', 'pl-PL')
    expect(objective).toHaveTextContent('Opisać procedurę ostracyzmu')
    for (const cell of first.querySelectorAll('td')) expect(cell).toHaveAttribute('data-label')
    expect(first.querySelector('.status')).toHaveClass('status', 'needs')
    expect(first.querySelector('.status')).toHaveTextContent('Needs work')
    expect(first.querySelector('.note')?.textContent).toBe('Prep. 8 s')
    expect(first.querySelector('td[data-label="Answers"]')).toHaveTextContent('3')
    expect(first.querySelector('td[data-label="Last"]')).toHaveTextContent('22 Oct')
    expect(first.querySelector('td[data-label="Recent factual"]')).toHaveTextContent('51 · 44 · 60')
    expect(first.querySelector('td[data-label="Delivery"]')).toHaveTextContent('Prep. 8 s')

    const second = rows[1]
    expect(second.querySelector('.status')).toHaveClass('improving')
    expect(within(second).getByText('with help')).toHaveClass('note')
    expect(second.querySelector('td[data-label="Delivery"]')).toHaveTextContent('—')

    expect(rows[2].querySelector('.status')).toHaveClass('strong')
    const none = rows[3]
    expect(none.querySelector('.status')).toHaveClass('none')
    expect(none.querySelector('.status')).toHaveTextContent('Not practised')
    for (const label of ['Last', 'Recent factual', 'Delivery'])
      expect(none.querySelector(`td[data-label="${label}"]`)).toHaveTextContent(/^—$/)
    expect(none.querySelector('td[data-label="Answers"]')).toHaveTextContent('0')
    expect(within(none).queryByText('with help')).not.toBeInTheDocument()
  })

  it('names the whole-topic line in English, outside the Polish content', async () => {
    setupCoverageWorld({
      coverage: {
        3: subjectCoverage({
          topics: [
            topicCoverage({
              status: 'improving',
              objectives: [objectiveCoverage({ status: 'improving' }), notPractised({ objective_id: null, text: null })],
            }),
          ],
        }),
      },
    })
    renderPage()
    const cell = (await screen.findByText('Questions on the whole topic')).closest('td') as HTMLElement
    expect(cell).toHaveClass('obj')
    expect(cell).not.toHaveClass('content')
    expect(cell).not.toHaveAttribute('lang')
  })

  it('shows 20 topics, then the rest on request', async () => {
    const topics = Array.from({ length: 23 }, (_, n) =>
      topicCoverage({
        topic_id: 100 + n,
        title: `Temat numer ${n + 1}`,
        objectives: [objectiveCoverage({ objective_id: 1000 + n, text: `Cel numer ${n + 1}` })],
      }),
    )
    setupCoverageWorld({ coverage: { 3: subjectCoverage({ topics }) } })
    renderPage()
    await screen.findByRole('table')
    expect(document.querySelectorAll('tbody th[colspan="6"]')).toHaveLength(20)
    await userEvent.click(screen.getByRole('button', { name: 'Show 3 more topics' }))
    expect(document.querySelectorAll('tbody th[colspan="6"]')).toHaveLength(23)
    expect(screen.queryByRole('button', { name: /more topic/ })).not.toBeInTheDocument()
  })

  it('has one primary action to practise the weak and unpractised objectives', async () => {
    setupCoverageWorld()
    renderPage()
    await screen.findByRole('table')
    const primary = document.querySelectorAll('.btn.primary')
    expect(primary).toHaveLength(1)
    expect(primary[0]).toHaveTextContent('Practise Needs work and Not practised')
    expect(primary[0]).toHaveAttribute('href', '/practice?subject=3&type=adaptive&sources=all')
    expect(screen.getByRole('link', { name: 'My materials' })).not.toHaveAttribute('aria-current')
    expect(screen.queryByText(/Practise with/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Trends' })).toHaveAttribute('href', '/progress/trends?subject=3')
  })

  it('opens the first subject without a ?subject and writes it into the address', async () => {
    const world = setupCoverageWorld()
    renderPage('/progress/coverage')
    await screen.findByRole('table')
    expect(where()).toBe('/progress/coverage?subject=3')
    expect(world.requests.filter((url) => url.startsWith('/api/coverage'))).toEqual(['/api/coverage/subjects/3?layer=all'])
  })

  it('loads the other subject when the select changes', async () => {
    const world = setupCoverageWorld()
    renderPage()
    await screen.findByRole('table')
    await userEvent.selectOptions(screen.getByLabelText('Subject'), 'Biologia komórki')
    await waitFor(() => expect(world.requests).toContain('/api/coverage/subjects/4?layer=all'))
    expect(where()).toBe('/progress/coverage?subject=4')
    expect(await screen.findByRole('heading', { name: 'No topics yet' })).toBeInTheDocument()
  })

  it('says so when there is no subject, and points to the Library', async () => {
    setupCoverageWorld({ subjects: [], coverage: {} })
    renderPage('/progress/coverage')
    expect(await screen.findByRole('heading', { name: 'No subjects yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to the Library' })).toHaveAttribute('href', '/library')
    expect(screen.queryByLabelText('Subject')).not.toBeInTheDocument()
  })

  it('says so when the subject has no topics, and points to the subject', async () => {
    setupCoverageWorld({ coverage: { 3: emptySubjectCoverage(3) } })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'No topics yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the subject' })).toHaveAttribute('href', '/library/3')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows a status while loading and an error notice when the load fails', async () => {
    setupCoverageWorld()
    server.use(http.get('/api/coverage/subjects/:id', () => HttpResponse.json({ detail: 'Database busy.' }, { status: 500 })))
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Loading the coverage…')
    expect(await screen.findByText('Database busy.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('keeps Polish content in lang="pl-PL" without breakable one-letter words', async () => {
    setupCoverageWorld({
      coverage: {
        3: subjectCoverage({
          topics: [
            topicCoverage({
              title: 'Zgromadzenie i sądy w Atenach',
              objectives: [objectiveCoverage({ text: 'Opisać rolę rady w Atenach i w Sparcie' })],
            }),
          ],
        }),
      },
    })
    renderPage()
    await screen.findByRole('table')
    const polish = [...document.querySelectorAll('[lang="pl-PL"]')].filter((node) => node.tagName !== 'OPTION')
    expect(polish.length).toBeGreaterThanOrEqual(2)
    for (const node of polish) expect(node.textContent ?? '').not.toMatch(BREAKABLE)
  })
})


describe('CoveragePage: Sources', () => {
  const MATERIALS = subjectCoverage({
    layer: 'materials',
    line_total: 1,
    line_counts: { strong: 0, improving: 0, needs_work: 1, not_practised: 0 },
    topics: [
      topicCoverage({
        topic_id: 7,
        title: 'Ostracyzm',
        status: 'needs_work',
        objectives: [objectiveCoverage({ objective_id: 70, text: 'Wyjaśnić cel ostracyzmu', status: 'needs_work' })],
      }),
    ],
  })

  function layeredWorld(coverage = MATERIALS) {
    const world = setupCoverageWorld()
    server.use(
      http.get('/api/coverage/subjects/:id', ({ request }) => {
        const url = new URL(request.url)
        world.requests.push(url.pathname + url.search)
        return HttpResponse.json(url.searchParams.get('layer') === 'all' ? subjectCoverage() : coverage)
      }),
    )
    return world
  }

  it('switches between My materials and All questions, All questions by default', async () => {
    layeredWorld()
    renderPage()
    await screen.findByRole('table')
    const nav = screen.getByRole('navigation', { name: 'Sources' })
    expect(nav).toHaveClass('seg')
    const materials = within(nav).getByRole('link', { name: 'My materials' })
    const all = within(nav).getByRole('link', { name: 'All questions' })
    expect(all).toHaveAttribute('aria-current', 'page')
    expect(materials).not.toHaveAttribute('aria-current')
    await userEvent.click(materials)
    expect(await screen.findByRole('link', { name: 'Ostracyzm' })).toBeInTheDocument()
    expect(where()).toBe('/progress/coverage?subject=3&layer=materials')
  })

  it('keeps the materials layer when the subject changes', async () => {
    const world = layeredWorld()
    renderPage('/progress/coverage?subject=3&layer=materials')
    await screen.findByRole('table')
    await userEvent.selectOptions(screen.getByLabelText('Subject'), 'Biologia komórki')
    await waitFor(() => expect(world.requests).toContain('/api/coverage/subjects/4'))
    expect(where()).toBe('/progress/coverage?subject=4&layer=materials')
  })

  it('reads an old ?layer=expanded link as All questions', async () => {
    const world = layeredWorld()
    renderPage('/progress/coverage?subject=3&layer=expanded')
    await screen.findByRole('table')
    expect(world.requests.filter((url) => url.startsWith('/api/coverage'))).toEqual(['/api/coverage/subjects/3?layer=all'])
    expect(within(screen.getByRole('navigation', { name: 'Sources' })).getByRole('link', { name: 'All questions' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(screen.queryByText('Knowledge beyond your documents')).not.toBeInTheDocument()
  })

  it('keeps the layer in the practise action and on the topic links of My materials', async () => {
    layeredWorld()
    renderPage('/progress/coverage?subject=3&layer=materials')
    await screen.findByRole('table')
    expect(screen.getByRole('link', { name: 'Ostracyzm' })).toHaveAttribute('href', '/progress/topics/7?layer=materials')
    const primary = document.querySelectorAll('.btn.primary')
    expect(primary).toHaveLength(1)
    expect(primary[0]).toHaveAttribute('href', '/practice?subject=3&type=adaptive&sources=materials')
  })

  it('shows the same map, with its Delivery column, in both layers', async () => {
    const world = layeredWorld()
    renderPage()
    await screen.findByRole('table')
    expect(world.requests.filter((url) => url.startsWith('/api/coverage'))).toEqual(['/api/coverage/subjects/3?layer=all'])
    expect(screen.getByLabelText('Learning objectives by status')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Coverage map' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Delivery' })).toBeInTheDocument()
    expect(document.querySelectorAll('tbody th[colspan="6"]').length).toBeGreaterThan(0)
  })
})
