import { render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { CoverageBox } from './CoverageBox'
import { emptySubjectCoverage, setupCoverageWorld, subjectCoverage } from './coverage.mocks'

function renderBox(id = 3) {
  return render(
    <MemoryRouter>
      <CoverageBox subjectId={id} />
    </MemoryRouter>,
  )
}

describe('CoverageBox', () => {
  it('counts the topics per status', async () => {
    setupCoverageWorld({
      coverage: {
        3: subjectCoverage({
          topic_counts: { strong: 3, improving: 1, needs_work: 0, not_practised: 2 },
        }),
      },
    })
    renderBox()
    const box = (await screen.findByRole('heading', { name: 'Coverage' })).closest('section') as HTMLElement
    expect(box).toHaveClass('box', 'tint')
    const list = box.querySelector('dl.facts') as HTMLElement
    const terms = [...list.querySelectorAll('dt')]
    expect(terms.map((dt) => dt.querySelector('.status')?.textContent)).toEqual([
      'Strong',
      'Improving',
      'Needs work',
      'Not practised',
    ])
    expect(terms.map((dt) => dt.querySelector('.status')?.className)).toEqual([
      'status strong',
      'status improving',
      'status needs',
      'status none',
    ])
    expect([...list.querySelectorAll('dd')].map((dd) => dd.textContent)).toEqual([
      '3 topics',
      '1 topic',
      '0 topics',
      '2 topics',
    ])
  })

  it('links to the coverage map of the subject', async () => {
    setupCoverageWorld()
    renderBox()
    const link = await screen.findByRole('link', { name: 'Open the coverage map' })
    expect(link).toHaveAttribute('href', '/progress/coverage?subject=3')
  })

  it('renders nothing while loading', () => {
    setupCoverageWorld()
    const { container } = renderBox()
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the load fails', async () => {
    server.use(http.get('/api/coverage/subjects/:id', () => HttpResponse.json({ detail: 'Busy.' }, { status: 500 })))
    const { container } = renderBox()
    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(screen.queryByText('Busy.')).not.toBeInTheDocument()
  })

  it('renders nothing when the subject has no topics', async () => {
    const world = setupCoverageWorld({ coverage: { 3: emptySubjectCoverage(3) } })
    const { container } = renderBox()
    await waitFor(() => expect(world.requests).toContain('/api/coverage/subjects/3?layer=all'))
    await waitFor(() => expect(within(container).queryByRole('heading')).not.toBeInTheDocument())
    expect(container).toBeEmptyDOMElement()
  })
})
