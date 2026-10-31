import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { Question, SourceChange } from './api'
import { QuestionCard } from './QuestionCard'

const OLD = 'Do ważności głosowania potrzeba było co najmniej 6000 głosów.'
const NEW = 'Do ważności głosowania potrzeba było 6000 głosów oddanych łącznie.'

function change(patch: Partial<SourceChange> = {}): SourceChange {
  return {
    document_id: 1,
    document_title: 'Ateny',
    old_version: 2,
    new_version: 3,
    old: { id: 100, locator: 's. 14 ¶2', heading: null, text: OLD },
    new: { id: 200, locator: 's. 15 ¶1', heading: null, text: NEW },
    ...patch,
  }
}

function question(patch: Partial<Question> = {}): Question {
  return {
    version_id: 7,
    family_id: 7,
    version: 1,
    text: 'Ile głosów było potrzebnych, aby ostracyzm był ważny?',
    style: 'short',
    difficulty: 'recall',
    primary_topic_id: 5,
    status: 'needs_review',
    review_note: 'source changed: „Ateny” version 3',
    rubric: {
      essential_points: [
        { id: 'p1', text: '6000 głosów', weight: 0.5, alternatives: [], passage_ids: [100] },
        { id: 'p2', text: 'Ważność głosowania', weight: 0.5, alternatives: [], passage_ids: [100] },
      ],
      enrichment_points: [],
      misconceptions: [],
      major_error_rules: [],
      model_answer: 'Co najmniej 6000 głosów.',
      expanded_explanation: null,
    },
    passages: [{ id: 100, locator: 's. 14 ¶2', heading: null, text: OLD }],
    source_changes: [change()],
    origin: 'ai',
    ...patch,
  }
}

function renderCard(q: Question, handlers: Partial<{ onEdited: (q: Question) => void }> = {}) {
  return render(
    <QuestionCard
      question={q}
      review={undefined}
      onReview={() => undefined}
      onEdited={handlers.onEdited ?? (() => undefined)}
      onRetire={async () => undefined}
    />,
  )
}

describe('QuestionCard: changed sources', () => {
  it('compares the cited passage in both versions with the changed words marked', () => {
    renderCard(question())
    const used = screen.getByRole('heading', { name: 'Passage in version 2 (used now)' })
    const newer = screen.getByRole('heading', { name: 'Passage in version 3' })
    const oldQuote = used.parentElement!.querySelector('blockquote.source-quote')!
    const newQuote = newer.parentElement!.querySelector('blockquote.source-quote')!
    expect(oldQuote.querySelector('span.source')).toHaveTextContent('s. 14 ¶2')
    expect(newQuote.querySelector('span.source')).toHaveTextContent('s. 15 ¶1')
    expect(oldQuote.querySelector('p')).toHaveAttribute('lang', 'pl-PL')
    expect(newQuote.querySelector('p')).toHaveAttribute('lang', 'pl-PL')
    expect(oldQuote.querySelector('p')!.textContent).toBe(OLD)
    expect(newQuote.querySelector('p')!.textContent).toBe(NEW)
    const removed = [...oldQuote.querySelectorAll('mark')].map((m) => m.textContent!.trim())
    const added = [...newQuote.querySelectorAll('mark')].map((m) => m.textContent!.trim())
    expect(removed).toEqual(['co', 'najmniej', 'głosów.'])
    expect(added).toEqual(['głosów', 'oddanych', 'łącznie.'])
  })

  it('says when the passage was removed', () => {
    renderCard(question({ source_changes: [change({ new: null })] }))
    expect(screen.getByRole('heading', { name: 'Removed in version 3' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Passage in version 3' })).toBeNull()
    expect(document.querySelectorAll('blockquote.source-quote mark')).toHaveLength(0)
  })

  it('explains the comparison and the keep choice', () => {
    renderCard(question())
    expect(
      screen.getByText('Changed words are marked. Answers given to this question so far stay linked to version 2.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Keep it if the question and its key points are still correct with the new passage.'),
    ).toBeInTheDocument()
  })

  it('keeps the question: the returned version replaces the card', async () => {
    const posted: string[] = []
    server.use(
      http.post('/api/questions/:id/keep', ({ params }) => {
        posted.push(String(params.id))
        return HttpResponse.json(
          question({ version_id: 8, version: 2, status: 'active', review_note: null, source_changes: [], origin: 'kept' }),
          { status: 201 },
        )
      }),
    )
    const onEdited = vi.fn()
    renderCard(question(), { onEdited })
    const keep = screen.getByRole('button', { name: 'Keep (still correct)' })
    expect(keep).toHaveClass('btn')
    expect(keep).not.toHaveClass('primary')
    await userEvent.click(keep)
    expect(posted).toEqual(['7'])
    expect(onEdited).toHaveBeenCalledWith(expect.objectContaining({ version_id: 8, status: 'active' }))
  })

  it('shows the refusal of a removed passage and leaves the card', async () => {
    const message =
      'This question cites a passage that is no longer in the document. Edit it to choose another passage, or retire it.'
    server.use(http.post('/api/questions/:id/keep', () => HttpResponse.json({ detail: message }, { status: 409 })))
    const onEdited = vi.fn()
    renderCard(question({ source_changes: [change({ new: null })] }), { onEdited })
    await userEvent.click(screen.getByRole('button', { name: 'Keep (still correct)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(onEdited).not.toHaveBeenCalled()
  })

  it('keeps Edit and Retire, and offers Keep only with changed sources', () => {
    const view = renderCard(question())
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retire' })).toBeInTheDocument()
    view.unmount()
    renderCard(question({ source_changes: [] }))
    expect(screen.queryByRole('button', { name: 'Keep (still correct)' })).toBeNull()
    expect(screen.queryByText(/Changed words are marked/)).toBeNull()
  })

  it('labels the version by its origin', () => {
    const label = (q: Question) => {
      const view = renderCard(q)
      const text = within(screen.getByRole('article')).getByText(/^Version/).textContent
      view.unmount()
      return text
    }
    expect(label(question())).toBe('Version 1')
    expect(label(question({ version: 2, origin: 'user' }))).toBe('Version 2 (edited)')
    expect(label(question({ version: 2, origin: 'kept' }))).toBe('Version 2 (kept after a document change)')
    expect(label(question({ origin: undefined }))).toBe('Version 1')
  })

  it('has no primary button', () => {
    renderCard(question())
    expect(document.querySelector('.btn.primary')).toBeNull()
  })
})
