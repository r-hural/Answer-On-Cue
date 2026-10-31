import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { job, topic } from '../library/library.mocks'
import { intervals } from '../library/strings'
import { server } from '../../test/msw'
import { QuickPage } from './QuickPage'
import { remember } from './quickState'
import { requestsTo, setupQuick, topics } from './quick.mocks'

function Where() {
  return <p data-testid="where">{useLocation().pathname + useLocation().search}</p>
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/quick/5']}>
      <Routes>
        <Route path="/quick/:subjectId" element={<QuickPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

const original = intervals.jobs
beforeEach(() => {
  intervals.jobs = 20
  sessionStorage.clear()
})
afterEach(() => {
  intervals.jobs = original
})

/** Watches the page and tells whether `text` was ever shown, even for one render. */
function watchFor(text: string) {
  let seen = false
  const observer = new MutationObserver((records) => {
    if (document.body.textContent?.includes(text)) seen = true
    for (const record of records)
      for (const node of record.addedNodes) if (node.textContent?.includes(text)) seen = true
  })
  observer.observe(document.body, { childList: true, subtree: true, characterData: true })
  return {
    seen: () => seen,
    stop: () => observer.disconnect(),
  }
}

const EMPTY = 'No questions could be prepared from these topics.'

const extractDone = job({ id: 7, kind: 'topics.extract', status: 'done', steps_done: 4 })
const withQuestions = () => [
  topic({ id: 11, title: 'Budowa błony', question_count: 3 }),
  topic({ id: 12, title: 'Transport przez błonę', question_count: 3 }),
  topic({ id: 13, title: 'Mitochondria', question_count: 0 }),
]

describe('QuickPage: preparing questions', () => {
  it('shows the requested count, a meter and a chip per topic', async () => {
    remember(5, { count: 8, topicIds: [11, 12, 13] })
    setupQuick({
      topics: [
        topic({ id: 11, title: 'Budowa błony', question_count: 3 }),
        topic({ id: 12, title: 'Transport przez błonę', question_count: 0 }),
        topic({ id: 13, title: 'Mitochondria', question_count: 0 }),
      ],
      jobs: [job({ id: 8, kind: 'questions.generate', status: 'running', steps_done: 1, steps_total: 4 }), extractDone],
      jobPolls: [job({ id: 8, kind: 'questions.generate', status: 'running', steps_done: 1, steps_total: 4 })],
    })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Preparing 8 questions…' })).toBeInTheDocument()
    const bar = screen.getByRole('progressbar', { name: 'Questions prepared' })
    await waitFor(() => expect(bar).toHaveAttribute('aria-valuenow', '25'))
    const rows = within(screen.getByRole('complementary', { name: 'Topics in this set' })).getAllByRole('listitem')
    expect(within(rows[0]).getByText('Done')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Preparing')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Waiting')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Home meanwhile' })).toHaveAttribute('href', '/')
  })

  it('says "Preparing questions…" when the count is not remembered', async () => {
    setupQuick({
      topics: topics(),
      jobs: [job({ id: 8, kind: 'questions.generate', status: 'running' }), extractDone],
      jobPolls: [job({ id: 8, kind: 'questions.generate', status: 'running' })],
    })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Preparing questions…' })).toBeInTheDocument()
  })

  it('moves to the ready step when the job ends, without a reload', async () => {
    const world = setupQuick({
      topics: withQuestions(),
      jobs: [job({ id: 8, kind: 'questions.generate', status: 'running' }), extractDone],
      jobPolls: [
        job({ id: 8, kind: 'questions.generate', status: 'running' }),
        job({ id: 8, kind: 'questions.generate', status: 'done', steps_done: 4 }),
      ],
    })
    renderPage()
    await screen.findByRole('heading', { name: 'Preparing questions…' })
    const empty = watchFor(EMPTY)
    // A slow count: the step must not be decided before it comes in.
    server.use(
      http.get('/api/questions', async () => {
        await delay(100)
        return HttpResponse.json(Array.from({ length: world.activeQuestions }, () => ({})))
      }),
    )
    world.jobs = [job({ id: 8, kind: 'questions.generate', status: 'done', steps_done: 4 }), extractDone]
    world.activeQuestions = 6
    expect(await screen.findByText('6 questions are ready')).toBeInTheDocument()
    empty.stop()
    expect(empty.seen()).toBe(false)
  })

  it('offers the topics again when a stopped job cannot be resumed', async () => {
    setupQuick({
      topics: topics(),
      jobs: [
        job({ id: 8, kind: 'questions.generate', status: 'failed', resumable: false, error_message: 'Quota.' }),
        extractDone,
      ],
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Quota.')
    expect(screen.getByRole('link', { name: 'Back' })).toHaveClass('primary')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Back to topics' }))
    expect(await screen.findByRole('heading', { name: /Topics found in/ })).toBeInTheDocument()
  })

  it('offers "Resume" for a stopped job', async () => {
    const world = setupQuick({
      topics: topics(),
      jobs: [
        job({ id: 8, kind: 'questions.generate', status: 'failed', resumable: true, error_message: 'Stopped.' }),
        extractDone,
      ],
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Stopped.')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(requestsTo(world, 'POST', /jobs\/8\/resume$/)).toHaveLength(1))
  })
})

describe('QuickPage: ready', () => {
  const ready = (patch = {}) =>
    setupQuick({
      topics: withQuestions(),
      activeQuestions: 6,
      jobs: [job({ id: 8, kind: 'questions.generate', status: 'done' }), extractDone],
      ...patch,
    })

  it('states the question count and the round settings from Settings', async () => {
    ready()
    renderPage()
    expect(await screen.findByText('6 questions are ready')).toBeInTheDocument()
    expect(screen.getByText('Practice mode: feedback after each answer')).toBeInTheDocument()
    expect(screen.getByText('30 s to prepare, 2 minutes to answer')).toBeInTheDocument()
    expect(screen.getByText('Read aloud (Piper, local voice)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Change settings first' })).toHaveAttribute('href', '/practice?subject=5')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('starts the round with the quick-practice settings and opens the microphone check', async () => {
    const world = ready()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Start' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('/practice/77/microphone')
    expect(requestsTo(world, 'POST', /^\/api\/rounds$/)[0].body).toEqual({
      subject_id: 5,
      config: {
        round_type: 'standard',
        scope: { kind: 'topics', topic_ids: [11, 12], document_ids: [] },
        source_policy: 'materials',
        feedback_timing: 'practice',
        question_count: 6,
        style: 'mixed',
        difficulty: 'mixed',
        prep_seconds: 30,
        answer_seconds: 120,
        question_audio: { mode: 'premium' },
      },
    })
  })

  it('shows the server reason when the round cannot start', async () => {
    ready({ roundStatus: 409 })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Start' }))
    expect(await screen.findByText('Not enough questions for this round.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled()
  })

  it('renames the subject and shows that saving into an existing one is a later version', async () => {
    const world = ready()
    renderPage()
    const name = await screen.findByLabelText('Subject name')
    expect(name).toHaveValue('Biologia')
    expect(name).not.toHaveAttribute('lang')
    await userEvent.clear(name)
    await userEvent.type(name, 'Biologia komórki')
    await userEvent.click(screen.getByRole('button', { name: 'Save name' }))
    expect(await screen.findByText('Name saved.')).toBeInTheDocument()
    expect(requestsTo(world, 'PATCH', /subjects\/5$/)[0].body).toEqual({ name: 'Biologia komórki' })
    expect(screen.getByText('Later version')).toBeInTheDocument()
    expect(screen.getByText(/Adding it to an existing subject: later version/)).toBeInTheDocument()
  })

  it('warns when a finished generation made no questions, and goes back to the topics', async () => {
    ready({ activeQuestions: 0, topics: topics() })
    renderPage()
    expect(await screen.findByText('No questions could be prepared from these topics.')).toBeInTheDocument()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Back to topics' }))
    expect(await screen.findByRole('heading', { name: /Topics found in/ })).toBeInTheDocument()
  })

  it('decides between ready and empty only after a new generation is read back', async () => {
    const world = ready({ activeQuestions: 0, topics: topics() })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Back to topics' }))
    await screen.findByRole('heading', { name: /Topics found in/ })
    const finished = job({ id: 9, kind: 'questions.generate', status: 'done', steps_done: 4 })
    world.jobPolls = [finished]
    world.jobs = [finished, ...world.jobs]
    world.activeQuestions = 4
    const empty = watchFor(EMPTY)
    await userEvent.click(screen.getByRole('button', { name: 'Prepare questions' }))
    expect(await screen.findByText('4 questions are ready')).toBeInTheDocument()
    empty.stop()
    expect(empty.seen()).toBe(false)
  })
})
