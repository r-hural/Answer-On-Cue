import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Question } from './api'
import { QuestionCard } from './QuestionCard'

function question(source_policy?: string): Question {
  return {
    version_id: 1,
    family_id: 1,
    version: 1,
    text: 'Omów demokrację ateńską.',
    style: 'open',
    difficulty: 'recall',
    primary_topic_id: 5,
    status: 'active',
    review_note: null,
    rubric: {
      essential_points: [{ id: 'p1', text: 'Zgromadzenie', weight: 1, alternatives: [], passage_ids: [], basis: 'expanded' }],
      enrichment_points: [],
      misconceptions: [],
      major_error_rules: [],
      model_answer: 'Odpowiedź.',
      expanded_explanation: null,
    },
    passages: [],
    source_policy,
    basis: 'expanded',
  } as Question
}

function chips(policy?: string) {
  const view = render(<QuestionCard question={question(policy)} review={undefined} onReview={() => undefined} onEdited={() => undefined} onRetire={async () => undefined} />)
  const card = screen.getByRole('article')
  const texts = [...card.querySelectorAll('.chips .chip')].map((chip) => chip.textContent)
  view.unmount()
  return texts
}

describe('QuestionCard policy chip', () => {
  it('shows Only expand, My materials + expand, and nothing for my materials', () => {
    expect(chips('outside')).toContain('Only expand')
    expect(chips('expanded')).toContain('My materials + expand')
    const plain = chips('materials')
    expect(plain).not.toContain('Only expand')
    expect(plain).not.toContain('My materials + expand')
    expect(chips(undefined)).not.toContain('Only expand')
  })

  it('keeps the basis chip', () => {
    render(<QuestionCard question={question('outside')} review={undefined} onReview={() => undefined} onEdited={() => undefined} onRetire={async () => undefined} />)
    expect(within(screen.getByRole('article')).getByText('Expanded', { selector: '.chip' })).toBeInTheDocument()
  })
})
