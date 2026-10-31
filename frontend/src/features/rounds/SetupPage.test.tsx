import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { SetupPage } from './SetupPage'
import { setupRoundWorld, type RoundWorld } from './rounds.mocks'
import { defaultForm } from './setup'

const PREFS = {
  transcription_model: 'gpt-transcribe',
  generation_provider: 'openai',
  generation_model: 'gen-1',
  assessment_provider: 'anthropic',
  assessment_model: 'asm-1',
  voice_provider: 'browser',
  voice_name: '',
  voice_rate: 1,
  factual_weights: [30, 50],
  factual_band_bounds: [30, 60, 80],
}

const topic = (id: number, title: string, count: number, children: unknown[] = []) => ({
  id,
  title,
  description: null,
  origin: 'extracted',
  status: 'active',
  objectives: [],
  passage_count: 3,
  question_count: count,
  children,
})

const question = (id: number, style: string, difficulty: string, topicId: number | null) => ({
  version_id: id,
  family_id: id,
  version: 1,
  text: `Pytanie ${id}`,
  style,
  difficulty,
  primary_topic_id: topicId,
  status: 'active',
})

interface Setup {
  questions?: ReturnType<typeof question>[]
  voices?: unknown[]
}

function mockApi({ questions, voices }: Setup = {}): RoundWorld {
  const world = setupRoundWorld()
  const list =
    questions ??
    Array.from({ length: 12 }, (_, i) => question(i + 1, i % 2 ? 'open' : 'short', i < 8 ? 'recall' : 'apply', i < 6 ? 11 : 12))
  server.use(
    http.get('/api/subjects', () => HttpResponse.json([{ id: 1, name: 'Demokracja w Atenach', description: null, document_count: 1 }, { id: 2, name: 'Biologia', description: null, document_count: 0 }])),
    http.get('/api/settings', () => HttpResponse.json(PREFS)),
    http.get('/api/voice/voices', () => HttpResponse.json(voices ?? [])),
    http.get('/api/topics', () =>
      HttpResponse.json([
        topic(11, 'Reformy Klejstenesa i w Atenach', 6, [topic(13, 'Fyle', 2)]),
        topic(12, 'Ostracyzm', 6),
      ]),
    ),
    http.get('/api/subjects/:id/documents', () =>
      HttpResponse.json([{ id: 21, title: 'Ateny', latest_version: { passage_count: 5 }, version_count: 1 }]),
    ),
    http.get('/api/questions', () => HttpResponse.json(list)),
    http.get('/api/subjects/:id', () => HttpResponse.json({ id: 1, name: 'Demokracja w Atenach' })),
  )
  return world
}

function renderPage(path = '/practice') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/practice" element={<SetupPage />} />
        <Route path="/practice/:roundId/microphone" element={<p>Microphone page</p>} />
        <Route path="/settings" element={<p>Settings page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
})

describe('SetupPage: round defaults from the settings', () => {
  it('uses the settings values for the count and times when no form is stored', async () => {
    mockApi()
    server.use(
      http.get('/api/settings', () =>
        HttpResponse.json({ ...PREFS, round_question_count: 5, round_prep_seconds: 15, round_answer_seconds: 90 }),
      ),
    )
    renderPage()
    expect(await screen.findByLabelText('Subject')).toHaveValue('1')
    await waitFor(() => expect(screen.getByLabelText('Main questions')).toHaveValue(5))
    expect(screen.getByLabelText('Preparation (s)')).toHaveValue(15)
    expect(screen.getByLabelText('Answer time (s)')).toHaveValue(90)
  })

  it('keeps 10 / 30 / 120 when the settings have no such fields', async () => {
    mockApi()
    renderPage()
    expect(await screen.findByLabelText('Subject')).toHaveValue('1')
    expect(screen.getByLabelText('Main questions')).toHaveValue(10)
    expect(screen.getByLabelText('Preparation (s)')).toHaveValue(30)
    expect(screen.getByLabelText('Answer time (s)')).toHaveValue(120)
  })

  it('does not override a stored form', async () => {
    mockApi()
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify({ questionCount: 7, prepSeconds: 20, answerSeconds: 60 }))
    server.use(
      http.get('/api/settings', () =>
        HttpResponse.json({ ...PREFS, round_question_count: 5, round_prep_seconds: 15, round_answer_seconds: 90 }),
      ),
    )
    renderPage()
    expect(await screen.findByLabelText('Subject')).toHaveValue('1')
    expect(screen.getByLabelText('Main questions')).toHaveValue(7)
  })

  it('keeps a value typed before the settings arrive', async () => {
    const world = mockApi()
    let release = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.get('/api/settings', async () => {
        await gate
        return HttpResponse.json({ ...PREFS, round_question_count: 5, round_prep_seconds: 30, round_answer_seconds: 90 })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    const prep = await screen.findByLabelText('Preparation (s)')
    await user.clear(prep)
    await user.type(prep, '10')
    release()
    await waitFor(() => expect(screen.getByLabelText('Main questions')).toHaveValue(5))
    expect(screen.getByLabelText('Preparation (s)')).toHaveValue(10)
    expect(screen.getByLabelText('Answer time (s)')).toHaveValue(90)

    const start = await screen.findByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect(post?.body).toMatchObject({ config: { prep_seconds: 10, question_count: 5, answer_seconds: 90 } })
  })
})

describe('SetupPage', () => {
  it('renders the defaults of the table', async () => {
    mockApi()
    renderPage()
    expect(await screen.findByLabelText('Subject')).toHaveValue('1')
    expect(screen.getByRole('radio', { name: 'Whole subject' })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Standard/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Practice/ })).toBeChecked()
    expect(screen.getByLabelText('Main questions')).toHaveValue(10)
    expect(screen.getByLabelText('Style')).toHaveValue('mixed')
    expect(screen.getByLabelText('Difficulty')).toHaveValue('mixed')
    expect(screen.getByLabelText('Preparation (s)')).toHaveValue(30)
    expect(screen.getByText('0 = answer at once')).toBeInTheDocument()
    expect(screen.getByLabelText('Answer time (s)')).toHaveValue(120)
    // the default question audio comes from the settings voice provider
    expect(screen.getByLabelText('Read questions aloud')).toHaveValue('browser')
    expect(await screen.findByText('All settings work together.')).toBeInTheDocument()
    expect(screen.getByText('About 29 minutes')).toBeInTheDocument()
    expect(screen.getByText(/Questions: OpenAI gen-1 · assessment: Anthropic asm-1 · transcription: OpenAI gpt-transcribe\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Change in Settings' })).toHaveAttribute('href', '/settings')
  })

  it('preselects the subject from the query', async () => {
    mockApi()
    renderPage('/practice?subject=2')
    await waitFor(() => expect(screen.getByLabelText('Subject')).toHaveValue('2'))
  })

  it('disables the later-version options and marks them', async () => {
    mockApi()
    renderPage()
    await screen.findByLabelText('Subject')
    const later: [string, 'radio' | 'checkbox'][] = [
      ['Benchmark', 'radio'],
      ['Grade each answer with two models', 'checkbox'],
    ]
    for (const [name, role] of later) {
      const control = screen.getByRole(role, { name: new RegExp(name) })
      expect(control).toBeDisabled()
      expect(within(control.closest('label') as HTMLElement).getByText('Later version')).toBeInTheDocument()
    }
  })

  it('marks topic titles, not subject names, as Polish content', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByLabelText('Subject')).not.toHaveAttribute('lang')
    await user.click(screen.getByRole('radio', { name: 'Selected topics' }))
    const title = await screen.findByText(/Reformy Klejstenesa/)
    expect(title).toHaveAttribute('lang', 'pl-PL')
    expect(title.textContent).toBe('Reformy Klejstenesa i w Atenach')
  })

  it('lists active topics with children indented and disables start while none is selected', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(screen.getByRole('radio', { name: 'Selected topics' }))
    const list = await screen.findByRole('list', { name: 'Topics in this round' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[1]).toHaveClass('child')
    expect(within(rows[0]).getByText('6 questions')).toBeInTheDocument()
    expect(await screen.findByText('Select at least one topic.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeDisabled()
    await user.click(within(rows[0]).getByRole('checkbox'))
    await user.click(within(rows[2]).getByRole('checkbox'))
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeEnabled()
  })

  it('says when the subject has no questions yet', async () => {
    mockApi({ questions: [] })
    renderPage()
    expect(await screen.findByText('This subject has no questions yet.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the Library' })).toHaveAttribute('href', '/library/1')
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeDisabled()
  })

  it('explains too few matches and "Ask A questions" sets the count', async () => {
    mockApi({ questions: [question(1, 'open', 'recall', 11), question(2, 'open', 'recall', 11), question(3, 'short', 'apply', 11)] })
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.selectOptions(screen.getByLabelText('Difficulty'), 'recall')
    expect(await screen.findByText('Only 2 questions match.')).toBeInTheDocument()
    expect(screen.getByText(/“Basic recall” in the whole subject gives 2 questions, not 10\./)).toBeInTheDocument()
    expect(screen.queryByText(/Generate/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow any style' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Ask 2 questions' }))
    expect(screen.getByLabelText('Main questions')).toHaveValue(2)
    expect(screen.getByText('All settings work together.')).toBeInTheDocument()
  })

  it('offers "Allow any difficulty" and removes the filter', async () => {
    mockApi({ questions: [question(1, 'open', 'recall', 11)] })
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.selectOptions(screen.getByLabelText('Difficulty'), 'analysis')
    await user.click(await screen.findByRole('button', { name: 'Allow any difficulty' }))
    expect(screen.getByLabelText('Difficulty')).toHaveValue('mixed')
    expect(await screen.findByText('Only 1 question matches.')).toBeInTheDocument()
  })

  it('shows the available count a 409 reports for the documents scope', async () => {
    const world = mockApi()
    world.createError = { status: 409, detail: { message: 'Not enough questions for the selected settings: 3 available.', available: 3 } }
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(screen.getByRole('radio', { name: 'Selected documents' }))
    expect(await screen.findByText('Select at least one document.')).toBeInTheDocument()
    await user.click(await screen.findByRole('checkbox', { name: /Ateny/ }))
    await user.click(screen.getByRole('button', { name: 'Check microphone and start' }))
    expect(await screen.findByText('Only 3 questions match.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ask 3 questions' })).toBeInTheDocument()
  })

  it('shows a 422 message with a link to Settings', async () => {
    const world = mockApi()
    world.createError = { status: 422, detail: 'Choose a premium voice first.' }
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(await screen.findByRole('button', { name: 'Check microphone and start' }))
    expect(await screen.findByText('Choose a premium voice first.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Settings' })).toHaveAttribute('href', '/settings')
  })

  it('posts the exact RoundCreate and opens the microphone check', async () => {
    const world = mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(screen.getByRole('radio', { name: /Exam/ }))
    await user.selectOptions(screen.getByLabelText('Style'), 'open')
    await user.selectOptions(screen.getByLabelText('Read questions aloud'), 'off')
    const count = screen.getByLabelText('Main questions')
    await user.clear(count)
    await user.type(count, '3')
    const prep = screen.getByLabelText('Preparation (s)')
    await user.clear(prep)
    await user.type(prep, '10')
    const answer = screen.getByLabelText('Answer time (s)')
    await user.clear(answer)
    await user.type(answer, '45')
    const start = await screen.findByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect(post?.body).toEqual({
      subject_id: 1,
      config: {
        round_type: 'standard',
        scope: { kind: 'subject', topic_ids: [], document_ids: [] },
        source_policy: 'all',
        feedback_timing: 'exam',
        question_count: 3,
        style: 'open',
        difficulty: 'mixed',
        prep_seconds: 10,
        answer_seconds: 45,
        question_audio: { mode: 'off' },
        follow_ups: { enabled: true, prep_seconds: 15, answer_seconds: 60 },
        extension: { enabled: false, question_count: 3 },
        immediate_repeat: false,
        retry_round: true,
      },
    })
    expect(JSON.parse(sessionStorage.getItem('aoc.practiceForm') as string)).toMatchObject({ feedback: 'exam', questionCount: 3 })
  })
})

describe('SetupPage sources', () => {
  const pool = (expanded: number) => [
    ...Array.from({ length: 12 }, (_, i) => ({ ...question(i + 1, 'open', 'recall', 11), source_policy: 'materials' })),
    ...Array.from({ length: expanded }, (_, i) => ({ ...question(100 + i, 'open', 'recall', 12), source_policy: 'expanded' })),
  ]

  it('offers both source policies, All questions by default', async () => {
    mockApi({ questions: pool(4) })
    renderPage()
    await screen.findByLabelText('Subject')
    const group = screen.getByRole('radiogroup', { name: 'Questions' })
    const own = within(group).getByRole('radio', { name: /My materials/ })
    const expand = within(group).getByRole('radio', { name: /All questions/ })
    expect(own).toBeEnabled()
    expect(own).not.toBeChecked()
    expect(expand).toBeEnabled()
    expect(expand).toBeChecked()
    expect(within(expand.closest('label') as HTMLElement).queryByText('Later version')).toBeNull()
    expect(screen.getByText(/· All questions$/)).toBeInTheDocument()
  })

  it('posts source_policy "materials" and names it in the summary', async () => {
    const world = mockApi({ questions: pool(4) })
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(screen.getByRole('radio', { name: /My materials/ }))
    expect(screen.getByText(/· My materials$/)).toBeInTheDocument()
    const count = screen.getByLabelText('Main questions')
    await user.clear(count)
    await user.type(count, '3')
    const start = screen.getByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect(post?.body).toMatchObject({ config: { source_policy: 'materials', question_count: 3 } })
  })

  it('preselects All questions from ?sources=expanded, and settings arriving later do not undo a click', async () => {
    mockApi({ questions: pool(4) })
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.get('/api/settings', async () => {
        await gate
        return HttpResponse.json(PREFS)
      }),
    )
    const user = userEvent.setup()
    renderPage('/practice?sources=expanded')
    const expand = await screen.findByRole('radio', { name: /All questions/ })
    expect(expand).toBeChecked()
    await user.click(screen.getByRole('radio', { name: /My materials/ }))
    release()
    await waitFor(() => expect(screen.getByLabelText('Subject')).toHaveValue('1'))
    expect(screen.getByRole('radio', { name: /My materials/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /All questions/ })).not.toBeChecked()
  })
})

describe('SetupPage follow-ups', () => {
  it('offers follow-up questions, on by default, with their own timers', async () => {
    mockApi()
    renderPage()
    await screen.findByLabelText('Subject')
    const box = screen.getByRole('checkbox', { name: /^Follow-up questions/ })
    expect(box).toBeEnabled()
    expect(box).toBeChecked()
    expect(within(box.closest('.choice') as HTMLElement).queryByText('Later version')).toBeNull()
    expect(screen.getByLabelText('Follow-up preparation in seconds')).toHaveValue(15)
    expect(screen.getByLabelText('Follow-up answer time in seconds')).toHaveValue(60)
  })

  it('unchecking disables the timers and the body carries the values', async () => {
    const world = mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.selectOptions(screen.getByLabelText('Read questions aloud'), 'off')
    const prep = screen.getByLabelText('Follow-up preparation in seconds')
    await user.clear(prep)
    await user.type(prep, '20')
    const answer = screen.getByLabelText('Follow-up answer time in seconds')
    await user.clear(answer)
    await user.type(answer, '45')
    await user.click(screen.getByRole('checkbox', { name: /^Follow-up questions/ }))
    expect(prep).toBeDisabled()
    expect(answer).toBeDisabled()
    const start = await screen.findByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect(post?.body).toMatchObject({ config: { follow_ups: { enabled: false, prep_seconds: 20, answer_seconds: 45 } } })
  })

  it('gives an old stored form the follow-up defaults', async () => {
    const old = { ...defaultForm(), subjectId: 1, questionCount: 4 } as Partial<ReturnType<typeof defaultForm>>
    delete old.followUps
    delete old.followUpPrepSeconds
    delete old.followUpAnswerSeconds
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify(old))
    mockApi()
    renderPage()
    expect(await screen.findByLabelText('Main questions')).toHaveValue(4)
    expect(screen.getByRole('checkbox', { name: /^Follow-up questions/ })).toBeChecked()
    expect(screen.getByLabelText('Follow-up preparation in seconds')).toHaveValue(15)
    expect(screen.getByLabelText('Follow-up answer time in seconds')).toHaveValue(60)
  })

  it('round defaults from the settings still fill only untouched fields', async () => {
    mockApi()
    server.use(http.get('/api/settings', () => HttpResponse.json({ ...PREFS, round_prep_seconds: 12 })))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Preparation (s)')).toHaveValue(12))
    expect(screen.getByLabelText('Follow-up preparation in seconds')).toHaveValue(15)
  })
})

describe('SetupPage: adaptive round and extension', () => {
  it('offers Adaptive as a choice, Standard by default', async () => {
    mockApi()
    renderPage()
    await screen.findByLabelText('Subject')
    const adaptive = screen.getByRole('radio', { name: /^Adaptive:/ })
    expect(adaptive).toBeEnabled()
    expect(adaptive).not.toBeChecked()
    expect(within(adaptive.closest('label') as HTMLElement).queryByText('Later version')).toBeNull()
    expect(screen.getByRole('radio', { name: /^Standard:/ })).toBeChecked()
    expect(screen.queryByText(/About 60% Needs work/)).toBeNull()
  })

  it('choosing Adaptive shows the mix and the summary says Adaptive', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(screen.getByRole('radio', { name: /^Adaptive:/ }))
    expect(
      screen.getByText(
        'About 60% Needs work, 25% Not practised and 15% review of stronger objectives, from your coverage. A subject without answers gets an even spread.',
      ),
    ).toHaveClass('hint')
    const summary = screen.getByRole('complementary')
    expect(within(summary).getByText(/^Adaptive · Practice feedback/)).toBeInTheDocument()
  })

  it('?type=adaptive preselects Adaptive', async () => {
    mockApi()
    renderPage('/practice?subject=1&type=adaptive')
    await waitFor(() => expect(screen.getByRole('radio', { name: /^Adaptive:/ })).toBeChecked())
  })

  it('the extension count is disabled until the extension is checked', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    const box = screen.getByRole('checkbox', { name: /^Adaptive extension/ })
    const count = screen.getByLabelText('Extra questions')
    expect(box).toBeEnabled()
    expect(box).not.toBeChecked()
    expect(count).toHaveValue(3)
    expect(count).toHaveClass('input', 'tiny')
    expect(count).toBeDisabled()
    expect(within(box.closest('.choice') as HTMLElement).queryByText('Later version')).toBeNull()
    await user.click(box)
    expect(count).toBeEnabled()
  })

  it('posts the round type and the extension', async () => {
    const world = mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.selectOptions(screen.getByLabelText('Read questions aloud'), 'off')
    await user.click(screen.getByRole('radio', { name: /^Adaptive:/ }))
    await user.click(screen.getByRole('checkbox', { name: /^Adaptive extension/ }))
    const count = screen.getByLabelText('Extra questions')
    await user.clear(count)
    await user.type(count, '5')
    const start = await screen.findByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect(post?.body).toMatchObject({
      config: { round_type: 'adaptive', extension: { enabled: true, question_count: 5 } },
    })
  })

  it('a stored form without the new keys loads with the defaults', async () => {
    const old = { ...defaultForm(), subjectId: 1, questionCount: 4 } as Partial<ReturnType<typeof defaultForm>>
    delete old.roundType
    delete old.extension
    delete old.extensionCount
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify(old))
    mockApi()
    renderPage()
    expect(await screen.findByLabelText('Main questions')).toHaveValue(4)
    expect(screen.getByRole('radio', { name: /^Standard:/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /^Adaptive extension/ })).not.toBeChecked()
    expect(screen.getByLabelText('Extra questions')).toHaveValue(3)
  })
})

describe('SetupPage: answer again and the retry round', () => {
  it('offers both, checked, with the existing labels', async () => {
    mockApi()
    renderPage()
    await screen.findByLabelText('Subject')
    for (const name of [/^Answer again right after feedback/, /^Retry round at the end/]) {
      const box = screen.getByRole('checkbox', { name })
      expect(box).toBeEnabled()
      expect(box).toBeChecked()
      expect(within(box.closest('label') as HTMLElement).queryByText('Later version')).toBeNull()
    }
  })

  it('Exam unchecks an untouched Answer again; checking it then shows the conflict', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    const again = screen.getByRole('checkbox', { name: /^Answer again/ })
    await user.click(screen.getByRole('radio', { name: /Exam/ }))
    expect(again).not.toBeChecked()
    expect(await screen.findByText('All settings work together.')).toBeInTheDocument()
    await user.click(again)
    expect(again).toBeChecked()
    expect(screen.queryByText('All settings work together.')).toBeNull()
    const notice = screen.getByText('“Answer again” needs feedback after each question.').closest('.notice') as HTMLElement
    expect(notice).toHaveClass('notice', 'warn')
    expect(within(notice).getByRole('button', { name: 'Switch to Practice feedback' })).toHaveClass('btn', 'small')
    expect(within(notice).getByRole('button', { name: 'Turn off “Answer again”' })).toHaveClass('btn', 'small')
    const chip = within(again.closest('label') as HTMLElement).getByText('Needs Practice feedback')
    expect(chip).toHaveClass('chip', 'warn')
    expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeDisabled()
  })

  it('Switch to Practice feedback selects Practice and clears the notice', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(screen.getByRole('radio', { name: /Exam/ }))
    await user.click(screen.getByRole('checkbox', { name: /^Answer again/ }))
    await user.click(screen.getByRole('button', { name: 'Switch to Practice feedback' }))
    expect(screen.getByRole('radio', { name: /Practice/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /^Answer again/ })).toBeChecked()
    expect(screen.queryByText('“Answer again” needs feedback after each question.')).toBeNull()
    expect(await screen.findByText('All settings work together.')).toBeInTheDocument()
  })

  it('Turn off Answer again unchecks it and clears the notice', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.click(screen.getByRole('radio', { name: /Exam/ }))
    await user.click(screen.getByRole('checkbox', { name: /^Answer again/ }))
    await user.click(screen.getByRole('button', { name: 'Turn off “Answer again”' }))
    expect(screen.getByRole('radio', { name: /Exam/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /^Answer again/ })).not.toBeChecked()
    expect(screen.queryByText('“Answer again” needs feedback after each question.')).toBeNull()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check microphone and start' })).toBeEnabled())
  })

  it('posts both fields', async () => {
    const world = mockApi()
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('Subject')
    await user.selectOptions(screen.getByLabelText('Read questions aloud'), 'off')
    await user.click(screen.getByRole('checkbox', { name: /^Retry round/ }))
    const start = await screen.findByRole('button', { name: 'Check microphone and start' })
    await waitFor(() => expect(start).toBeEnabled())
    await user.click(start)
    expect(await screen.findByText('Microphone page')).toBeInTheDocument()
    const post = world.requests.find((r) => r.method === 'POST' && r.path === '/api/rounds')
    expect(post?.body).toMatchObject({ config: { feedback_timing: 'practice', immediate_repeat: true, retry_round: false } })
  })

  it('a stored form without the new keys loads with the defaults', async () => {
    const old = { ...defaultForm(), subjectId: 1, questionCount: 4 } as Partial<ReturnType<typeof defaultForm>>
    delete old.immediateRepeat
    delete old.retryRound
    sessionStorage.setItem('aoc.practiceForm', JSON.stringify(old))
    mockApi()
    renderPage()
    expect(await screen.findByLabelText('Main questions')).toHaveValue(4)
    expect(screen.getByRole('checkbox', { name: /^Answer again/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /^Retry round/ })).toBeChecked()
  })
})
