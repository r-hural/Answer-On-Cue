import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { Question, Review, Verdict } from './api'
import { BankReviewPage } from './BankReviewPage'

function question(id: number, text: string, topic: number, patch: Partial<Question> = {}): Question {
  return {
    version_id: id,
    family_id: id,
    version: 1,
    text,
    style: 'open',
    difficulty: 'recall',
    primary_topic_id: topic,
    status: 'active',
    review_note: null,
    rubric: {
      essential_points: [
        { id: 'p1', text: 'Zgromadzenie ludowe', weight: 0.6, alternatives: [], passage_ids: [100] },
        { id: 'p2', text: 'Rada Pięciuset', weight: 0.4, alternatives: [], passage_ids: [100, 101] },
      ],
      enrichment_points: [],
      misconceptions: [],
      major_error_rules: [],
      model_answer: `Odpowiedź na: ${text}`,
      expanded_explanation: null,
    },
    passages: [
      { id: 100, locator: 'sekcja 1', heading: null, text: 'Ekklesia zbierała się na Pnyksie.' },
      { id: 101, locator: 'sekcja 2', heading: null, text: 'Rada liczyła pięciuset członków.' },
    ],
    ...patch,
  }
}

interface Backend {
  questions: Question[]
  reviews: Review[]
  puts: { id: number; body: { verdict: Verdict; note: string | null } }[]
  edits: { id: number; body: Record<string, unknown> }[]
  retired: number[]
}

function setup(): Backend {
  const state: Backend = {
    questions: [
      question(11, 'Omów demokrację ateńską.', 5),
      question(12, 'Kim byli stratedzy?', 5, { status: 'needs_review', review_note: 'Brak w źródle.' }),
      question(13, 'Kim byli heloci?', 6),
    ],
    reviews: [],
    puts: [],
    edits: [],
    retired: [],
  }
  server.use(
    http.get('/api/subjects/1', () => HttpResponse.json({ id: 1, name: 'Historia' })),
    http.get('/api/topics', () =>
      HttpResponse.json([
        { id: 5, title: 'Ateny' },
        { id: 6, title: 'Sparta' },
      ]),
    ),
    http.get('/api/questions', () => HttpResponse.json(state.questions.map((q) => ({ version_id: q.version_id })))),
    http.get('/api/questions/reviews', () => HttpResponse.json(state.reviews)),
    http.get('/api/questions/:id', ({ params }) =>
      HttpResponse.json(state.questions.find((q) => q.version_id === Number(params.id))),
    ),
    http.put('/api/questions/:id/review', async ({ request, params }) => {
      const body = (await request.json()) as { verdict: Verdict; note: string | null }
      const id = Number(params.id)
      state.puts.push({ id, body })
      const review: Review = { question_version_id: id, ...body }
      state.reviews = [...state.reviews.filter((r) => r.question_version_id !== id), review]
      return HttpResponse.json(review)
    }),
    http.put('/api/questions/:id', async ({ request, params }) => {
      const body = (await request.json()) as Record<string, unknown>
      const id = Number(params.id)
      state.edits.push({ id, body })
      const old = state.questions.find((q) => q.version_id === id)!
      const fresh = question(14, String(body.text), 5, { family_id: old.family_id, version: 2 })
      state.questions = state.questions.map((q) => (q.version_id === id ? fresh : q))
      return HttpResponse.json(fresh)
    }),
    http.post('/api/questions/:id/retire', ({ params }) => {
      state.retired.push(Number(params.id))
      state.questions = state.questions.filter((q) => q.version_id !== Number(params.id))
      return HttpResponse.json({})
    }),
  )
  return state
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/library/1/questions']}>
      <Routes>
        <Route path="/library/:subjectId/questions" element={<BankReviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('BankReviewPage', () => {
  it('groups questions by topic with points, model answer and passages', async () => {
    setup()
    renderPage()
    const ateny = await screen.findByRole('region', { name: 'Ateny' })
    expect(within(ateny).getByText('Omów demokrację ateńską.')).toBeInTheDocument()
    expect(within(ateny).getByText('Kim byli stratedzy?')).toBeInTheDocument()
    const sparta = screen.getByRole('region', { name: 'Sparta' })
    expect(within(sparta).getByText('Kim byli heloci?')).toBeInTheDocument()
    const first = screen.getByRole('article', { name: 'Omów demokrację ateńską.' })
    const points = within(first).getAllByRole('listitem')
    expect(points[0]).toHaveTextContent('Zgromadzenie ludowe weight 0.6 · sekcja 1')
    expect(points[1]).toHaveTextContent('Rada Pięciuset weight 0.4 · sekcja 1, sekcja 2')
    expect(within(first).getByText(/Odpowiedź na: Omów demokrację ateńską\./)).toBeInTheDocument()
    expect(within(first).getByText('Source passages (2)')).toBeInTheDocument()
    expect(within(first).getByText(/Ekklesia zbierała się na Pnyksie\./)).toBeInTheDocument()
    const flagged = screen.getByRole('article', { name: 'Kim byli stratedzy?' })
    expect(within(flagged).getByText('Needs review')).toBeInTheDocument()
    expect(within(flagged).getByText('Brak w źródle.')).toBeInTheDocument()
  })

  it('saves a verdict on click and the note on blur', async () => {
    const backend = setup()
    renderPage()
    const card = await screen.findByRole('article', { name: 'Omów demokrację ateńską.' })
    const weak = within(card).getByRole('radio', { name: 'Weak' })
    expect(weak).not.toBeChecked()
    await userEvent.click(weak)
    await waitFor(() => expect(weak).toBeChecked())
    expect(backend.puts[0]).toEqual({ id: 11, body: { verdict: 'weak', note: null } })
    const note = within(card).getByLabelText('Note')
    await userEvent.type(note, 'Za ogólne.')
    expect(backend.puts).toHaveLength(1)
    await userEvent.tab()
    await waitFor(() => expect(backend.puts).toHaveLength(2))
    expect(backend.puts[1]).toEqual({ id: 11, body: { verdict: 'weak', note: 'Za ogólne.' } })
    await userEvent.click(within(card).getByRole('radio', { name: 'Bad' }))
    await waitFor(() => expect(within(card).getByRole('radio', { name: 'Bad' })).toBeChecked())
    expect(within(card).getByRole('radio', { name: 'Weak' })).not.toBeChecked()
    expect(backend.puts[2].body).toEqual({ verdict: 'bad', note: 'Za ogólne.' })
  })

  it('updates the counter and filters the list', async () => {
    setup()
    renderPage()
    expect(await screen.findByText('Rated: 0 of 3')).toBeInTheDocument()
    const first = screen.getByRole('article', { name: 'Omów demokrację ateńską.' })
    await userEvent.click(within(first).getByRole('radio', { name: 'OK' }))
    const third = screen.getByRole('article', { name: 'Kim byli heloci?' })
    await userEvent.click(within(third).getByRole('radio', { name: 'Bad' }))
    expect(await screen.findByText('Rated: 2 of 3')).toBeInTheDocument()
    const filter = screen.getByRole('navigation', { name: 'Show' })
    await userEvent.click(within(filter).getByRole('link', { name: 'Not rated (1)' }))
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Kim byli stratedzy?'])
    await userEvent.click(within(filter).getByRole('link', { name: 'Weak and Bad (1)' }))
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Kim byli heloci?'])
    await userEvent.click(within(filter).getByRole('link', { name: 'Needs review (1)' }))
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Kim byli stratedzy?'])
    await userEvent.click(within(filter).getByRole('link', { name: 'All (3)' }))
    expect(screen.getAllByRole('article')).toHaveLength(3)
  })

  it('shows an edited question as a new version without a verdict', async () => {
    const backend = setup()
    renderPage()
    const card = await screen.findByRole('article', { name: 'Omów demokrację ateńską.' })
    await userEvent.click(within(card).getByRole('radio', { name: 'OK' }))
    await screen.findByText('Rated: 1 of 3')
    await userEvent.click(within(card).getByRole('button', { name: 'Edit' }))
    const text = within(card).getByLabelText('Question text')
    await userEvent.clear(text)
    await userEvent.type(text, 'Omów ustrój Aten.')
    const weight = within(card).getByLabelText('Weight of point 1')
    await userEvent.clear(weight)
    await userEvent.type(weight, '0.7')
    await userEvent.click(within(card).getByRole('button', { name: 'Save as new version' }))
    const edited = await screen.findByRole('article', { name: 'Omów ustrój Aten.' })
    expect(within(edited).getByRole('radio', { name: 'OK' })).not.toBeChecked()
    expect(screen.getByText('Rated: 0 of 3')).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Omów demokrację ateńską.' })).toBeNull()
    const sent = backend.edits[0]
    expect(sent.id).toBe(11)
    expect(sent.body).toMatchObject({ text: 'Omów ustrój Aten.', style: 'open', difficulty: 'recall' })
    const rubric = sent.body.rubric as { essential_points: { weight: number; id: string }[]; model_answer: string }
    expect(rubric.essential_points.map((p) => [p.id, p.weight])).toEqual([
      ['p1', 0.7],
      ['p2', 0.4],
    ])
  })

  it('retires a question after confirmation', async () => {
    const backend = setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    renderPage()
    const card = await screen.findByRole('article', { name: 'Kim byli heloci?' })
    await userEvent.click(within(card).getByRole('button', { name: 'Retire' }))
    expect(confirm).toHaveBeenCalledWith('Retire this question?')
    expect(backend.retired).toEqual([])
    await userEvent.click(within(card).getByRole('button', { name: 'Retire' }))
    await waitFor(() => expect(screen.queryByRole('article', { name: 'Kim byli heloci?' })).toBeNull())
    expect(backend.retired).toEqual([13])
    expect(screen.getByText('Rated: 0 of 2')).toBeInTheDocument()
  })

  it('links the Markdown export of the verdicts', async () => {
    setup()
    renderPage()
    const link = await screen.findByRole('link', { name: 'Download ratings (Markdown)' })
    expect(link).toHaveAttribute('href', '/api/questions/reviews/export?subject_id=1')
  })

  it('reaches every enabled control with the tab key', async () => {
    setup()
    renderPage()
    await screen.findByText('Rated: 0 of 3')
    const controls = [
      ...screen.getAllByRole('button'),
      ...screen.getAllByRole('link'),
      ...screen.getAllByRole('textbox'),
      ...screen.queryAllByRole('combobox'),
    ].filter((element) => !element.hasAttribute('disabled'))
    const reached = new Set<Element>()
    for (let i = 0; i < controls.length + 8; i++) {
      await userEvent.tab()
      if (document.activeElement) reached.add(document.activeElement)
    }
    for (const control of controls) expect(reached.has(control)).toBe(true)
  })
})

function expandedQuestion(): Question {
  const base = question(21, 'Wyjaśnij, czym był ostracyzm.', 5)
  return {
    ...base,
    source_policy: 'expanded',
    basis: 'mixed',
    rubric: {
      ...base.rubric,
      essential_points: [
        { id: 'p1', text: 'Głosowanie na skorupach', weight: 0.4, alternatives: [], passage_ids: [100], basis: 'source' },
        { id: 'p2', text: 'Wygnanie na dziesięć lat', weight: 0.3, alternatives: [], passage_ids: [101], basis: 'source' },
        { id: 'p3', text: 'Ostatni ostracyzm Hyperbolosa', weight: 0.3, alternatives: [], passage_ids: [], basis: 'expanded' },
      ],
      beyond_material: ['Hyperbolos około 417 roku p.n.e.'],
    },
  }
}

function issueQuestion(): Question {
  const base = question(22, 'Kiedy wprowadzono ostracyzm?', 6, {
    status: 'needs_review',
    review_note: 'Fragmenty podają różne daty.',
    basis: 'source',
  })
  return {
    ...base,
    rubric: {
      ...base.rubric,
      source_issues: [{ kind: 'contradiction', passage_ids: [100, 101], note: 'Fragmenty podają różne daty.' }],
    },
  }
}

function withSourceLabels(): Backend {
  const backend = setup()
  backend.questions = [
    { ...question(11, 'Omów demokrację ateńską.', 5), basis: 'source' },
    expandedQuestion(),
    issueQuestion(),
    { ...question(23, 'Porównaj Ateny i Spartę.', 6), basis: 'expanded' },
  ]
  return backend
}

describe('BankReviewPage: source labels', () => {
  it('gives every card a chip with its source label', async () => {
    withSourceLabels()
    renderPage()
    const plain = await screen.findByRole('article', { name: 'Omów demokrację ateńską.' })
    expect(within(plain).getByText('From my materials')).toHaveClass('chip')
    const mixed = screen.getByRole('article', { name: 'Wyjaśnij, czym był ostracyzm.' })
    expect(within(mixed).getByText('Mixed', { selector: '.chip' })).toBeInTheDocument()
    const expanded = screen.getByRole('article', { name: 'Porównaj Ateny i Spartę.' })
    expect(within(expanded).getByText('Expanded', { selector: '.chip' })).toBeInTheDocument()
  })

  it('filters the cards by the Source select', async () => {
    withSourceLabels()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('article', { name: 'Omów demokrację ateńską.' })
    const select = screen.getByLabelText('Source')
    expect([...(select as HTMLSelectElement).options].map((o) => o.text)).toEqual(['All', 'From my materials', 'Expanded', 'Mixed'])
    expect(screen.getAllByRole('article')).toHaveLength(4)
    await user.selectOptions(select, 'mixed')
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Wyjaśnij, czym był ostracyzm.'])
    await user.selectOptions(select, 'source')
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual([
      'Omów demokrację ateńską.',
      'Kiedy wprowadzono ostracyzm?',
    ])
    await user.selectOptions(select, 'expanded')
    expect(screen.getAllByRole('article')).toHaveLength(1)
  })

  it('marks expanded and mixed points and counts them', async () => {
    const backend = withSourceLabels()
    backend.questions[1].rubric.essential_points[1].basis = 'mixed'
    renderPage()
    const card = await screen.findByRole('article', { name: 'Wyjaśnij, czym był ostracyzm.' })
    const items = within(card).getAllByRole('listitem')
    expect(within(items[0]).queryByText(/beyond your materials/)).toBeNull()
    expect(within(items[1]).getByText('partly beyond your materials')).toHaveClass('note')
    expect(within(items[2]).getByText('beyond your materials')).toHaveClass('note')
    expect(items[2]).toHaveTextContent(/weight 0\.3 beyond your materials$/)
    expect(within(card).getByText('Expanded points are marked: 2 of 3 key points go beyond your materials.')).toBeInTheDocument()
  })

  it('says "goes" for one point beyond the materials', async () => {
    withSourceLabels()
    renderPage()
    const card = await screen.findByRole('article', { name: 'Wyjaśnij, czym był ostracyzm.' })
    expect(within(card).getByText('Expanded points are marked: 1 of 3 key points goes beyond your materials.')).toBeInTheDocument()
  })

  it('shows a source issue with its passages', async () => {
    withSourceLabels()
    renderPage()
    const card = await screen.findByRole('article', { name: 'Kiedy wprowadzono ostracyzm?' })
    const title = within(card).getByText('Your sources may disagree')
    const notice = title.closest('.notice') as HTMLElement
    expect(notice).toHaveClass('warn')
    expect(within(notice).getByText('Fragmenty podają różne daty.')).toHaveAttribute('lang', 'pl-PL')
    expect(notice.querySelectorAll('blockquote.source-quote')).toHaveLength(2)
  })

  it('posts every point basis unchanged when an expanded question is edited', async () => {
    const backend = withSourceLabels()
    const user = userEvent.setup()
    renderPage()
    const card = await screen.findByRole('article', { name: 'Wyjaśnij, czym był ostracyzm.' })
    await user.click(within(card).getByRole('button', { name: 'Edit' }))
    const text = within(card).getByLabelText('Question text')
    await user.clear(text)
    await user.type(text, 'Wyjaśnij cel ostracyzmu.')
    await user.click(within(card).getByRole('button', { name: 'Save as new version' }))
    await waitFor(() => expect(backend.edits).toHaveLength(1))
    const rubric = backend.edits[0].body.rubric as { essential_points: { basis: string; passage_ids: number[] }[]; beyond_material: string[] }
    expect(rubric.essential_points.map((p) => p.basis)).toEqual(['source', 'source', 'expanded'])
    expect(rubric.essential_points[2].passage_ids).toEqual([])
    expect(rubric.beyond_material).toEqual(['Hyperbolos około 417 roku p.n.e.'])
  })
})

describe('BankReviewPage: replaced documents', () => {
  const changed = (id: number) =>
    question(id, `Pytanie ${id}?`, 5, {
      status: 'needs_review',
      review_note: 'source changed: „Ateny” version 2',
      source_changes: [
        {
          document_id: 1,
          document_title: 'Ateny',
          old_version: 1,
          new_version: 2,
          old: { id: 100, locator: 'sekcja 1', heading: null, text: 'Ekklesia zbierała się na Pnyksie.' },
          new: { id: 200, locator: 'sekcja 1', heading: null, text: 'Ekklesia zbierała się na wzgórzu Pnyks.' },
        },
      ],
    })

  function renderAt(entry: string) {
    return render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/library/:subjectId/questions" element={<BankReviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('opens on the Needs review tab from ?filter=review', async () => {
    setup()
    renderAt('/library/1/questions?filter=review')
    const tab = await screen.findByRole('link', { name: 'Needs review (1)' })
    expect(tab).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByText('Omów demokrację ateńską.')).toBeNull()
    expect(screen.getByText('Kim byli stratedzy?')).toBeInTheDocument()
  })

  it('names the questions whose passages changed', async () => {
    const state = setup()
    state.questions = [...state.questions, changed(21), changed(22), changed(23)]
    renderAt('/library/1/questions?filter=review')
    const title = await screen.findByText('3 questions cite passages that changed')
    const notice = title.closest('.notice')!
    expect(notice).toHaveClass('notice', 'warn')
    expect(notice).toHaveTextContent('They are not asked in rounds until you keep, edit or retire them.')
    expect(document.querySelector('.btn.primary')).toBeNull()
  })

  it('shows no such notice without changed passages', async () => {
    setup()
    renderAt('/library/1/questions?filter=review')
    await screen.findByText('Kim byli stratedzy?')
    expect(screen.queryByText(/cite passages that changed/)).toBeNull()
  })
})
