import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import {
  historyEntry,
  notPractised,
  objectiveCoverage,
  setupCoverageWorld,
  topicDetail,
  topicSource,
} from './coverage.mocks'
import { subjectHandler } from '../language/language.mocks'
import { TopicCoveragePage } from './TopicCoveragePage'

const BREAKABLE = /(^|[\s(„"«])[aiouwzAIOUWZ] /

function renderPage(id = 7) {
  return render(
    <MemoryRouter initialEntries={[`/progress/topics/${id}`]}>
      <Routes>
        <Route path="/progress/topics/:topicId" element={<TopicCoveragePage />} />
        <Route path="/progress/coverage" element={<p>Map page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

// the coverage API has no language: the page asks for the subject (3 in the mocks)
beforeEach(() => {
  server.use(subjectHandler({ id: 3, language: 'pl' }))
})

const rows = () => [...document.querySelectorAll('ul.rows > li')] as HTMLElement[]
const historyRows = () => [...document.querySelectorAll('table.wide tbody tr')] as HTMLElement[]

describe('TopicCoveragePage', () => {
  it('shows the crumbs, the title and the meta line', async () => {
    setupCoverageWorld()
    renderPage()
    const title = await screen.findByRole('heading', { level: 1 })
    expect(title).toHaveClass('content')
    expect(title).toHaveAttribute('lang', 'pl-PL')
    expect(title).toHaveTextContent('Ostracyzm: procedura i cel')
    const crumbs = within(screen.getByRole('navigation', { name: 'Breadcrumb' }))
    const links = crumbs.getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual(['Progress', 'Demokracja ateńska'])
    for (const link of links) expect(link).toHaveAttribute('href', '/progress/coverage?subject=3')
    expect(crumbs.getByText('Ostracyzm: procedura i cel')).toHaveAttribute('lang', 'pl-PL')
    const meta = title.nextElementSibling as HTMLElement
    expect(meta).toHaveTextContent('2 learning objectives · 6 questions · terms: ostrakon, ostracyzm · sources: 2 passages')
    const terms = meta.querySelector('.content') as HTMLElement
    expect(terms).toHaveAttribute('lang', 'pl-PL')
    expect(terms).toHaveTextContent('ostrakon, ostracyzm')
  })

  it('has one primary action', async () => {
    setupCoverageWorld()
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const primary = document.querySelectorAll('.btn.primary')
    expect(primary).toHaveLength(1)
    expect(primary[0]).toHaveTextContent('Practise this subject')
    expect(primary[0]).toHaveAttribute('href', '/practice?subject=3&sources=all')
  })

  it('shows each objective with its status and the evidence behind it', async () => {
    setupCoverageWorld({
      topics: {
        7: topicDetail({
          objectives: [
            objectiveCoverage(),
            objectiveCoverage({
              objective_id: 2,
              text: 'Wyjaśnić cel polityczny ostracyzmu',
              status: 'improving',
              assisted: true,
              deciding: {
                attempt_id: 12,
                round_id: 6,
                at: '2026-10-22T12:00:00Z',
                band: 'good',
                factual: 78,
                independent: false,
              },
            }),
            objectiveCoverage({ objective_id: 3, text: 'Opisać głosowanie', status: 'strong' }),
            notPractised({ objective_id: 4, text: 'Ocenić skutki' }),
          ],
        }),
      },
    })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const [needs, assisted, strong, none] = rows()

    expect(needs.querySelector('.title')).toHaveClass('content')
    expect(needs.querySelector('.title')).toHaveAttribute('lang', 'pl-PL')
    expect(needs.querySelector('.status')).toHaveClass('needs')
    expect(needs.querySelector('.status')).toHaveTextContent('Needs work')
    expect(needs.querySelector('.meta')).toHaveTextContent('Latest independent answer: Needs improvement (51) on 22 Oct')
    expect(needs.querySelector('.meta .band')).toHaveClass('band', 'needs')
    expect(needs.querySelector('.chip')).toBeNull()
    expect(needs.querySelector('.note')).toHaveTextContent(
      'To become Strong: Very good in two independent rounds on different days.',
    )

    expect(assisted.querySelector('.status')).toHaveClass('improving')
    expect(assisted.querySelector('.chip')).toHaveTextContent('With help')
    expect(assisted.querySelector('.meta')).toHaveTextContent('Answered again with help: Good (78) on 22 Oct')
    expect(assisted.querySelector('.note')).toHaveTextContent(
      'Not yet confirmed on your own. Answer it independently to move it on.',
    )

    expect(strong.querySelector('.status')).toHaveClass('strong')
    expect(strong.querySelector('.note')).toHaveTextContent('Very good in two independent rounds on different days.')
    expect(strong.querySelector('.note')?.textContent).not.toMatch(/^To become/)

    expect(none.querySelector('.status')).toHaveClass('none')
    expect(none.querySelector('.meta')).toHaveTextContent('Not answered yet.')
    expect(none.querySelector('.note')).toHaveTextContent('Generated questions count only once you answer them.')
  })

  it('tells the improving hint for an improving line without help', async () => {
    setupCoverageWorld({
      topics: { 7: topicDetail({ objectives: [objectiveCoverage({ status: 'improving' })] }) },
    })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    expect(rows()[0].querySelector('.note')).toHaveTextContent('To become Strong')
  })

  it('names the whole-topic line without Polish markup', async () => {
    setupCoverageWorld({
      topics: { 7: topicDetail({ objectives: [notPractised({ objective_id: null, text: null })] }) },
    })
    renderPage()
    const title = (await screen.findByText('Questions on the whole topic')) as HTMLElement
    expect(title).toHaveClass('title')
    expect(title).not.toHaveAttribute('lang')
  })

  it('lists the answer history with attempt, result, scores, preparation and Open links', async () => {
    setupCoverageWorld({
      topics: {
        7: topicDetail({
          history: [
            historyEntry({
              attempt_id: 21,
              round_id: 9,
              attempt_kind: 'immediate',
              independent: false,
              band: 'good',
              factual: 78,
              language: 71,
              preparation_used_ms: 5000,
            }),
            historyEntry({ attempt_id: 20, round_id: 8 }),
            historyEntry({
              attempt_id: 19,
              round_id: 7,
              attempt_kind: 'retry',
              independent: false,
              difficulty: 'recall',
              band: 'good',
              factual: 60,
              language: null,
              preparation_used_ms: 2000,
            }),
            historyEntry({
              attempt_id: 18,
              round_id: 6,
              status: 'failed',
              outcome: 'answered',
              band: null,
              factual: null,
              language: null,
              preparation_used_ms: null,
            }),
            historyEntry({
              attempt_id: 17,
              round_id: 5,
              status: 'processing',
              band: null,
              factual: null,
              language: null,
              preparation_used_ms: null,
            }),
            historyEntry({
              attempt_id: 16,
              round_id: 4,
              status: 'interrupted',
              outcome: 'interrupted',
              difficulty: 'analysis',
              band: null,
              factual: null,
              language: null,
              preparation_used_ms: null,
            }),
          ],
        }),
      },
    })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const table = document.querySelector('table.wide.dense.middle') as HTMLElement
    expect([...table.querySelectorAll('thead th')].map((th) => th.textContent)).toEqual([
      'Date',
      'Question and difficulty',
      'Attempt',
      'Result',
      'Factual',
      'Language',
      'Prep. used',
      '',
    ])
    const [again, own, retry, failed, processing, interrupted] = historyRows()

    expect(again.querySelector('td.nowrap.num')).toHaveTextContent('22 Oct')
    expect(again.querySelector('.q-col p.content')).toHaveAttribute('lang', 'pl-PL')
    expect(again.querySelector('.q-col .note')).toHaveTextContent('Explanation and application')
    expect(again.querySelector('.line-stack')).toHaveTextContent('Answer again')
    expect(again.querySelector('.line-stack .chip')).toHaveTextContent('With help')
    expect(again.querySelector('.band')).toHaveClass('band', 'good')
    expect([...again.querySelectorAll('td.r')].map((td) => td.textContent)).toEqual(['78', '71', '5 s'])
    expect(within(again).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/rounds/9/answers/21')

    expect(own.querySelectorAll('td')[2]).toHaveTextContent(/^On your own$/)
    expect(own.querySelector('.band')).toHaveClass('needs')
    expect([...own.querySelectorAll('td.r')].map((td) => td.textContent)).toEqual(['51', '66', '8 s'])

    expect(retry.querySelector('.q-col .note')).toHaveTextContent('Basic recall')
    expect(retry.querySelector('.line-stack')).toHaveTextContent('Retry round')
    expect(retry.querySelector('.line-stack .note')).toHaveTextContent('own score')
    expect([...retry.querySelectorAll('td.r')].map((td) => td.textContent)).toEqual(['60', '—', '2 s'])

    expect(failed.querySelector('.chip.error')).toHaveTextContent('Unresolved')
    expect([...failed.querySelectorAll('td.r')].map((td) => td.textContent)).toEqual(['—', '—', '—'])
    expect(within(failed).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/rounds/6/answers/18')
    expect(processing.querySelector('.chip')).toHaveTextContent('Processing')
    expect(within(processing).queryByRole('link')).not.toBeInTheDocument()
    expect(interrupted.querySelector('.chip.warn')).toHaveTextContent('Interrupted')
    expect(interrupted.querySelector('.q-col .note')).toHaveTextContent('Deeper analysis')
    expect(within(interrupted).queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText(/A retry round keeps its own score/)).toBeInTheDocument()
  })

  it('says so when there is no answer yet', async () => {
    setupCoverageWorld({ topics: { 7: topicDetail({ history: [] }) } })
    renderPage()
    expect(await screen.findByText('No answers on this topic yet.')).toBeInTheDocument()
    expect(document.querySelector('table.wide')).toBeNull()
  })

  it('quotes the sources and links to the library', async () => {
    setupCoverageWorld()
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const quotes = [...document.querySelectorAll('blockquote.source-quote')] as HTMLElement[]
    expect(quotes).toHaveLength(2)
    expect(quotes[0].querySelector('.source')).toHaveTextContent(
      'Demokracja_atenska_rozdzialy_1-4.pdf · version 1 · s. 14 ¶1',
    )
    expect(quotes[0].querySelector('.source .file-name')).toBeInTheDocument()
    expect(quotes[0].querySelector('p')).toHaveAttribute('lang', 'pl-PL')
    expect(quotes[0].querySelector('p')).toHaveTextContent('Raz w roku zgromadzenie')
    expect(screen.getByRole('link', { name: 'Open the document' })).toHaveAttribute('href', '/library/3')
  })

  it('leaves out the sources box when there are none', async () => {
    setupCoverageWorld({ topics: { 7: topicDetail({ sources: [], passage_count: 0 }) } })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    expect(document.querySelector('blockquote')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Sources' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open the document' })).not.toBeInTheDocument()
  })

  it('shows an error notice with a way back when the topic is unknown', async () => {
    setupCoverageWorld({ topics: {} })
    renderPage(9)
    expect(await screen.findByText('Topic not found.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the coverage map' })).toHaveAttribute('href', '/progress/coverage')
  })

  it('shows the server message when the load fails and a status while loading', async () => {
    server.use(http.get('/api/coverage/topics/:id', () => HttpResponse.json({ detail: 'Database busy.' }, { status: 500 })))
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Loading the topic…')
    expect(await screen.findByText('Database busy.')).toBeInTheDocument()
  })

  it('has no delivery chart and keeps Polish text free of breakable one-letter words', async () => {
    setupCoverageWorld({
      topics: {
        7: topicDetail({
          history: [historyEntry({ question_text: 'Co zrobił Klejstenes w Atenach i w Sparcie?' })],
          sources: [topicSource({ excerpt: 'Raz w roku zgromadzenie decydowało o ostracyzmie i o jego skutkach.' })],
        }),
      },
    })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    expect(document.querySelector('svg.trend')).toBeNull()
    const polish = [...document.querySelectorAll('[lang="pl-PL"]')]
    expect(polish.length).toBeGreaterThanOrEqual(5)
    for (const node of polish) expect(node.textContent ?? '').not.toMatch(BREAKABLE)
  })
})


describe('TopicCoveragePage: layers', () => {
  function renderAt(entry: string) {
    render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/progress/topics/:topicId" element={<TopicCoveragePage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('reads an old ?layer=expanded link as All questions and links back to the map', async () => {
    const world = setupCoverageWorld({ topics: { 7: topicDetail() } })
    renderAt('/progress/topics/7?layer=expanded')
    const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumb' })
    expect(world.requests).toContain('/api/coverage/topics/7?layer=all')
    for (const link of within(crumbs).getAllByRole('link')) {
      expect(link).toHaveAttribute('href', '/progress/coverage?subject=3')
    }
  })

  it('keeps My materials in the requests and links', async () => {
    const world = setupCoverageWorld({ topics: { 7: topicDetail({ layer: 'materials' }) } })
    renderAt('/progress/topics/7?layer=materials')
    const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumb' })
    expect(world.requests).toContain('/api/coverage/topics/7')
    for (const link of within(crumbs).getAllByRole('link')) {
      expect(link).toHaveAttribute('href', '/progress/coverage?subject=3&layer=materials')
    }
    expect(document.querySelector('.btn.primary')).toHaveAttribute('href', '/practice?subject=3&sources=materials')
  })
})

describe('a given-up answer in the history', () => {
  it('shows "Not assessed" and still opens its feedback', async () => {
    setupCoverageWorld({
      topics: {
        7: topicDetail({
          history: [
            historyEntry({
              attempt_id: 31,
              round_id: 12,
              outcome: 'given_up',
              status: 'not_assessed',
              band: null,
              factual: null,
              language: null,
              preparation_used_ms: null,
            }),
          ],
        }),
      },
    })
    renderPage()
    await screen.findByRole('heading', { level: 1 })
    const [row] = historyRows()
    const chip = within(row).getByText('Not assessed')
    expect(chip).toHaveClass('chip')
    expect(chip).not.toHaveClass('error')
    expect(row.querySelector('.band')).toBeNull()
    expect(within(row).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/rounds/12/answers/31')
  })
})
