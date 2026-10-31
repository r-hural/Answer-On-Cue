import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { TrendChart, type TrendChartProps } from './TrendChart'
import { scoreAxis, washes } from './trendGeometry'

function rounds(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: 10 + i,
    date: `${12 + i} Oct`,
    title: `${12 + i} Oct: Knowledge and language ${50 + i}`,
    count: 10,
  }))
}

function chart(patch: Partial<TrendChartProps> = {}, n = 7) {
  const values = Array.from({ length: n }, (_, i) => 50 + i)
  const axis = scoreAxis(values)
  const props: TrendChartProps = {
    label: 'Knowledge and language per round',
    rounds: rounds(n),
    axis,
    format: String,
    lines: [{ values, axis }],
    ...patch,
  }
  const view = render(
    <MemoryRouter>
      <TrendChart {...props} />
    </MemoryRouter>,
  )
  return view.container
}

describe('TrendChart', () => {
  it('renders the scroller and the svg of the template', () => {
    const root = chart()
    const box = root.querySelector('div.chart-scroll')
    expect(box).toHaveAttribute('tabindex', '0')
    expect(box).toHaveAttribute('role', 'group')
    const svg = box?.querySelector('svg.trend.wide')
    expect(svg).toHaveAttribute('role', 'group')
    expect(svg).toHaveAttribute('aria-label', 'Knowledge and language per round')
    expect(svg).toHaveAttribute('viewBox', '0 0 520 214')
  })

  it('keeps the round points exposed as links inside the labelled chart', () => {
    chart()
    const svg = screen.getByRole('group', { name: 'Knowledge and language per round' })
    expect(screen.queryByRole('img')).toBeNull()
    const links = within(svg).getAllByRole('link')
    expect(links).toHaveLength(7)
    expect(links[0]).toHaveAttribute('href', '/rounds/10/summary')
    expect(links[6]).toHaveAccessibleName('18 Oct: Knowledge and language 56')
  })

  it('draws a polyline per segment with the style class, and a gap for a missing value', () => {
    const axis = scoreAxis([40, 60])
    const root = chart({
      axis,
      lines: [
        { values: [50, 52, 54, 56, 58, 60, 62], axis },
        { values: [45, 47, null, 49, 50, 51, 52], axis, style: 'dash' },
        { values: [45, null, 47, null, 49, null, 51], axis, style: 'dot soft' },
      ],
    })
    expect(root.querySelectorAll('polyline.line')).toHaveLength(1 + 2)
    expect(root.querySelectorAll('polyline.line.dash')).toHaveLength(2)
    expect(root.querySelectorAll('polyline.line.dot.soft')).toHaveLength(0)
    // every value of the dotted series is alone: a point each, no line
    expect(root.querySelectorAll('circle.pt[aria-hidden="true"]').length).toBeGreaterThanOrEqual(4)
  })

  it('draws bars, and washes only inside the range', () => {
    const axis = scoreAxis([45, 69])
    const root = chart({
      axis,
      lines: [{ values: [45, 50, 55, 60, 65, 69, 62], axis }],
      bars: { values: [45, null, 55, 60, 65, 69, 62], axis: { min: 0, max: 100, ticks: [0, 50, 100] } },
      washes: washes(axis, [30, 60, 80]),
    })
    expect(root.querySelectorAll('rect.bar')).toHaveLength(6)
    expect(root.querySelectorAll('rect.wash.needs')).toHaveLength(1)
    expect(root.querySelectorAll('rect.wash.good')).toHaveLength(1)
    expect(root.querySelector('rect.wash.weak')).toBeNull()
    expect(root.querySelector('rect.wash.very-good')).toBeNull()
    expect(root.querySelector('text.wash-lbl.good')).toHaveTextContent('Good')
  })

  it('marks a change between two rounds and notes a trade-off', () => {
    const root = chart({
      marks: [{ index: 3, text: 'Band table changed' }],
      note: { index: 4, text: 'Sooner, but factual fell' },
    })
    expect(root.querySelector('line.mark')).toHaveAttribute('x1', '231.7')
    expect(root.querySelector('text.mk')).toHaveTextContent('Band table changed')
    expect(root.querySelectorAll('text.mk')).toHaveLength(2)
  })

  it('links every point to its round summary and marks the newest', () => {
    const root = chart()
    const links = root.querySelectorAll('svg a')
    expect(links).toHaveLength(7)
    expect(links[0]).toHaveAttribute('href', '/rounds/10/summary')
    expect(links[0].querySelector('title')).toHaveTextContent('12 Oct: Knowledge and language 50')
    expect(root.querySelectorAll('circle.pt.this')).toHaveLength(1)
    expect(links[6].querySelector('circle')).toHaveClass('pt', 'this')
    expect(root.querySelector('text.val')).toHaveTextContent('18 Oct')
  })

  it('leaves out the point of a round without a value', () => {
    const axis = scoreAxis([50, 60])
    const root = chart({ lines: [{ values: [50, null, 60, 55, 56, 57, 58], axis }], axis })
    expect(root.querySelectorAll('svg a')).toHaveLength(6)
  })

  it('writes the counts under the dates only when asked', () => {
    expect(chart({ counts: true }).querySelector('svg')).toHaveAttribute('viewBox', '0 0 520 228')
    expect(chart({ counts: true }).querySelectorAll('svg > g > text[y="220"]')).toHaveLength(7)
    expect(chart().querySelectorAll('text[y="220"]')).toHaveLength(0)
  })

  it('shows the scroll hint and a long chart only beyond seven rounds', () => {
    expect(chart().querySelector('.chart-hint')).toBeNull()
    const root = chart({}, 12)
    expect(root.querySelector('.chart-hint')).toHaveTextContent('Scroll back for earlier rounds.')
    expect(root.querySelector('.chart-scroll')).toHaveClass('long')
    expect(root.querySelector('svg')).toHaveAttribute('viewBox', `0 0 ${44 + 64 * 11 + 76} 214`)
  })
})
