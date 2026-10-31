import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { ContentLanguageProvider } from '../../language/ContentLanguage'
import type { AttemptReport } from '../reports'
import { ai, assessment, attemptReport, reportResult, setupReportsWorld } from '../reports.mocks'
import { FeedbackView } from './FeedbackView'
import { PointsPanel } from './PointsPanel'

type Outside = { quote: string; judgement: 'valid' | 'doubtful' | 'wrong'; note: string }

const VALID: Outside = { quote: 'bitwa pod Maratonem', judgement: 'valid', note: 'Trafne.' }
const DOUBTFUL: Outside = { quote: 'Temistokles zbudował flotę w rok', judgement: 'doubtful', note: 'Nie do sprawdzenia.' }
const WRONG: Outside = { quote: 'Ateny leżały w Tracji', judgement: 'wrong', note: 'Błąd.' }

function withOutside(outside: Outside[], dropMissing = true): AttemptReport {
  return attemptReport({
    ...(dropMissing ? { points: [] } : {}),
    result: reportResult({
      assessment: assessment({ ai: ai({ outside_material: outside }) }),
    }),
  })
}

function renderPanel(report: AttemptReport, code?: string) {
  const panel = <PointsPanel report={report} />
  return render(code ? <ContentLanguageProvider code={code}>{panel}</ContentLanguageProvider> : panel)
}

const section = (name: string) => screen.getByRole('heading', { name }).nextElementSibling as HTMLElement

beforeEach(() => {
  setupReportsWorld()
})

describe('PointsPanel: outside your materials', () => {
  it('shows an unsure claim once, under Extra details, marked check this', () => {
    renderPanel(withOutside([VALID, DOUBTFUL, WRONG]))
    const errors = section('Factual errors')
    expect(errors).toHaveTextContent('Ateny leżały w Tracji')
    expect(errors).not.toHaveTextContent(DOUBTFUL.quote)
    expect(errors).not.toHaveTextContent(VALID.quote)

    const extra = section('Extra details')
    const items = within(extra).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent(VALID.quote)
    expect(items[1]).toHaveTextContent(DOUBTFUL.quote)
    expect(items[0].querySelector('.chip')).toBeNull()
    const chip = within(items[1]).getByText('check this')
    expect(chip).toHaveClass('chip', 'warn')
    expect(chip).toHaveAttribute('lang', 'en')
    expect(chip).toHaveAttribute(
      'title',
      'Your materials do not cover this, and it could not be confirmed. Check it in a reliable source.',
    )
    const note = within(items[1]).getByText('outside your materials')
    expect(note).toHaveClass('note')
    expect(screen.getAllByText(new RegExp(DOUBTFUL.quote))).toHaveLength(1)
  })

  it('lists only a doubtful claim under Extra details', () => {
    renderPanel(withOutside([DOUBTFUL]))
    expect(section('Factual errors')).toHaveTextContent('None.')
    const extra = section('Extra details')
    expect(extra).toHaveTextContent(DOUBTFUL.quote)
    expect(within(extra).getByText('check this')).toBeInTheDocument()
  })

  it('keeps a wrong claim under Factual errors only', () => {
    renderPanel(withOutside([WRONG]))
    expect(section('Factual errors')).toHaveTextContent(WRONG.quote)
    expect(section('Extra details')).toHaveTextContent('None.')
    expect(screen.queryByText('check this')).toBeNull()
  })

  it('keeps the content language of the round', () => {
    renderPanel(
      withOutside([{ quote: 'the fleet', judgement: 'doubtful', note: '' }]),
      'en',
    )
    const item = within(section('Extra details')).getByRole('listitem')
    const content = item.querySelector('span.content')!
    expect(content).toHaveAttribute('lang', 'en-GB')
    expect(content.textContent).toContain('“the fleet”')
  })

  it('does not change the scores', () => {
    const scoresOf = (report: AttemptReport) => {
      const { unmount } = render(
        <MemoryRouter>
          <FeedbackView report={report} />
        </MemoryRouter>,
      )
      const text = screen.getByLabelText('Scores').textContent
      unmount()
      return text
    }
    const without = scoresOf(withOutside([VALID], false))
    const withDoubtful = scoresOf(withOutside([VALID, DOUBTFUL], false))
    expect(without).toBeTruthy()
    expect(withDoubtful).toBe(without)
  })
})
