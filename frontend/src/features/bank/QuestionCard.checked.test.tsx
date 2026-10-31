import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import type { Question } from './api'
import { BankReviewPage } from './BankReviewPage'
import { QuestionCard } from './QuestionCard'

const HINT =
  'Mark it as checked if the question and its key points are correct as they are. It is then asked in rounds again.'

function question(patch: Partial<Question> = {}): Question {
  return {
    version_id: 7,
    family_id: 7,
    version: 1,
    text: 'Kto rządził Atenami?',
    style: 'open',
    difficulty: 'recall',
    primary_topic_id: 5,
    status: 'needs_review',
    review_note: 'Źródła się wykluczają.',
    rubric: {
      essential_points: [
        { id: 'p1', text: 'Zgromadzenie ludowe', weight: 0.5, alternatives: [], passage_ids: [100] },
        { id: 'p2', text: 'Rada Pięciuset', weight: 0.5, alternatives: [], passage_ids: [100] },
      ],
      enrichment_points: [],
      misconceptions: [],
      major_error_rules: [],
      model_answer: 'Zgromadzenie i rada.',
      expanded_explanation: null,
      source_issues: [{ kind: 'contradiction', passage_ids: [100], note: 'Dwa zdania się wykluczają.' }],
    },
    passages: [{ id: 100, locator: 's. 1', heading: null, text: 'Ekklesia zbierała się na Pnyksie.' }],
    source_changes: [],
    origin: 'ai',
    ...patch,
  }
}

function renderCard(q: Question, onEdited: (q: Question) => void = () => undefined) {
  return render(
    <QuestionCard
      question={q}
      review={undefined}
      onReview={() => undefined}
      onEdited={onEdited}
      onRetire={async () => undefined}
    />,
  )
}

describe('QuestionCard: Mark as checked', () => {
  it('offers it on a held card without source changes', () => {
    renderCard(question())
    const button = screen.getByRole('button', { name: 'Mark as checked' })
    expect(button).toHaveClass('btn', 'small')
    expect(button).not.toHaveClass('primary')
    expect(screen.getByText(HINT)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Keep (still correct)' })).toBeNull()
    const actions = button.closest('.actions') as HTMLElement
    expect(within(actions).getAllByRole('button')[0]).toBe(button)
  })

  it('sends one request, hands the returned question on and disables the button meanwhile', async () => {
    const posted: string[] = []
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post('/api/questions/:id/checked', async ({ params }) => {
        posted.push(String(params.id))
        await gate
        return HttpResponse.json(question({ status: 'active', review_note: null }))
      }),
    )
    const edited: Question[] = []
    renderCard(question(), (q) => edited.push(q))
    await userEvent.click(screen.getByRole('button', { name: 'Mark as checked' }))
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    release()
    await waitFor(() => expect(edited).toHaveLength(1))
    expect(posted).toEqual(['7'])
    expect(edited[0].status).toBe('active')
    expect(edited[0].version_id).toBe(7)
  })

  it('shows a refusal in the error notice', async () => {
    server.use(
      http.post('/api/questions/:id/checked', () =>
        HttpResponse.json({ detail: 'This question is not waiting for review.' }, { status: 409 }),
      ),
    )
    renderCard(question())
    await userEvent.click(screen.getByRole('button', { name: 'Mark as checked' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This question is not waiting for review.')
  })

  it('leaves a card with source changes to Keep and an active card to neither', () => {
    const change = {
      document_id: 1,
      document_title: 'Ateny',
      old_version: 1,
      new_version: 2,
      old: { id: 100, locator: 's. 1', heading: null, text: 'Stary tekst.' },
      new: { id: 200, locator: 's. 1', heading: null, text: 'Nowy tekst.' },
    }
    const { unmount } = renderCard(question({ source_changes: [change] }))
    expect(screen.getByRole('button', { name: 'Keep (still correct)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mark as checked' })).toBeNull()
    unmount()
    renderCard(question({ status: 'active', review_note: null }))
    expect(screen.queryByRole('button', { name: 'Mark as checked' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Keep (still correct)' })).toBeNull()
    expect(screen.queryByText(HINT)).toBeNull()
  })
})

describe('Mark as checked on the bank page', () => {
  function setup() {
    const state = { questions: [question()] }
    server.use(
      http.get('/api/subjects/1', () => HttpResponse.json({ id: 1, name: 'Historia' })),
      http.get('/api/topics', () => HttpResponse.json([{ id: 5, title: 'Ateny' }])),
      http.get('/api/questions', () => HttpResponse.json(state.questions.map((q) => ({ version_id: q.version_id })))),
      http.get('/api/questions/reviews', () => HttpResponse.json([])),
      http.get('/api/questions/:id', () => HttpResponse.json(state.questions[0])),
      http.post('/api/questions/:id/checked', () => {
        state.questions = [question({ status: 'active', review_note: null })]
        return HttpResponse.json(state.questions[0])
      }),
      http.put('/api/questions/:id', () => {
        state.questions = [question({ version_id: 8, version: 2, status: 'active', review_note: null, origin: 'user' })]
        return HttpResponse.json(state.questions[0])
      }),
    )
  }

  function renderAt(entry: string) {
    return render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/library/:subjectId/questions" element={<BankReviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('moves the card out of the Needs review tab and shows it as Active on All', async () => {
    setup()
    renderAt('/library/1/questions?filter=review')
    expect(await screen.findByRole('link', { name: 'Needs review (1)' })).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: 'Mark as checked' }))
    expect(await screen.findByRole('link', { name: 'Needs review (0)' })).toBeInTheDocument()
    expect(screen.queryByText('Kto rządził Atenami?')).toBeNull()
    await userEvent.click(screen.getByRole('link', { name: /^All/ }))
    const card = await screen.findByRole('article', { name: 'Kto rządził Atenami?' })
    expect(within(card).getByText('Active')).toBeInTheDocument()
  })

  it('shows the saved edit as Active', async () => {
    setup()
    renderAt('/library/1/questions')
    const card = await screen.findByRole('article', { name: 'Kto rządził Atenami?' })
    expect(within(card).getByText('Needs review')).toBeInTheDocument()
    await userEvent.click(within(card).getByRole('button', { name: 'Edit' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save as new version' }))
    const saved = await screen.findByRole('article', { name: 'Kto rządził Atenami?' })
    await waitFor(() => expect(within(saved).getByText('Active')).toBeInTheDocument())
    expect(within(saved).queryByText('Needs review')).toBeNull()
  })
})
