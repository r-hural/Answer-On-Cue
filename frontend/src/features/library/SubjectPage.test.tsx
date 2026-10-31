import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { deletionPreview, doc, job, passages, setupWorld, topic, version, type World } from './library.mocks'
import { intervals } from './strings'
import { SubjectPage } from './SubjectPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/library/1']}>
      <Routes>
        <Route path="/library" element={<p>Subject list</p>} />
        <Route path="/library/:subjectId" element={<SubjectPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const original = intervals.jobs
beforeEach(() => {
  intervals.jobs = 20
})
afterEach(() => {
  intervals.jobs = original
})

function texts(world: World, method: string, path: RegExp) {
  return world.requests.filter((r) => r.method === method && path.test(r.path))
}

describe('SubjectPage: documents', () => {
  it('uploads a dropped file', async () => {
    const world = setupWorld()
    renderPage()
    const zone = await screen.findByRole('button', { name: /Drop a PDF, DOCX or TXT file/ })
    const file = new File(['abc'], 'nowy.txt', { type: 'text/plain' })
    fireEvent.drop(zone, { dataTransfer: { files: [file] } })
    expect(await screen.findByText('nowy')).toBeInTheDocument()
    expect(texts(world, 'POST', /\/subjects\/1\/documents$/)).toHaveLength(1)
    // the new document is opened: preview and passages are shown
    expect(await screen.findByText('Treść fragmentu 1.', { exact: false })).toBeInTheDocument()
  })

  it('shows the backend message when the upload is rejected', async () => {
    setupWorld({ uploadStatus: 422 })
    renderPage()
    const zone = await screen.findByRole('button', { name: /Drop a PDF/ })
    fireEvent.drop(zone, { dataTransfer: { files: [new File([''], 'pusty.txt')] } })
    expect(await screen.findByRole('alert')).toHaveTextContent('The file is empty.')
  })

  it('shows the extraction failure message of a stored document', async () => {
    setupWorld({
      documents: [
        doc({ latest_version: version({ status: 'failed', message: 'Scan without a text layer.' }) }),
      ],
    })
    renderPage()
    expect(await screen.findByText('Scan without a text layer.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Extract topics/ })).toBeDisabled()
  })

  it('suggests excluding the back matter and never excludes on its own', async () => {
    const world = setupWorld({
      documents: [doc()],
      backMatter: { passage_ids: [101, 102], count: 2, first_locator: 'sekcja 2', last_locator: 'sekcja 3', already_excluded: false },
    })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    expect(
      await screen.findByText(
        'The end of the document looks like footnotes, a bibliography or links (2 passages: sekcja 2 – sekcja 3). Exclude them when creating questions?',
      ),
    ).toBeInTheDocument()
    expect(texts(world, 'POST', /exclusion/)).toHaveLength(0)
    const banner = screen.getByRole('region', { name: 'Footnotes and bibliography' })
    await userEvent.click(within(banner).getByRole('button', { name: 'Exclude' }))
    expect(await screen.findByText('Excluded 2 passages.')).toBeInTheDocument()
    expect(texts(world, 'POST', /exclusion/)[0].body).toEqual({ passage_ids: [101, 102], excluded: true })
    expect(screen.getByRole('checkbox', { name: 'Exclude passage: sekcja 2' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Exclude passage: sekcja 1' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Restore' }))
    expect(await screen.findByText(/The end of the document looks/)).toBeInTheDocument()
    expect(texts(world, 'POST', /exclusion/)[1].body).toEqual({ passage_ids: [101, 102], excluded: false })
  })

  it('"Keep" hides the suggestion without excluding anything', async () => {
    const world = setupWorld({
      documents: [doc()],
      backMatter: { passage_ids: [102], count: 1, first_locator: 'sekcja 3', last_locator: 'sekcja 3', already_excluded: false },
    })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Keep' }))
    expect(screen.queryByText(/The end of the document looks/)).toBeNull()
    expect(texts(world, 'POST', /exclusion/)).toHaveLength(0)
  })

  it('shows no banner when the document has no back matter', async () => {
    setupWorld({ documents: [doc()] })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    await screen.findByText('Passages')
    await screen.findByText('Showing 3 of 3')
    expect(screen.queryByRole('region', { name: 'Footnotes and bibliography' })).toBeNull()
  })

  it('toggles a single passage', async () => {
    const world = setupWorld({ documents: [doc()] })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    const box = await screen.findByRole('checkbox', { name: 'Exclude passage: sekcja 1' })
    await userEvent.click(box)
    await waitFor(() => expect(box).toBeChecked())
    expect(texts(world, 'PATCH', /\/passages\/100$/)[0].body).toEqual({ excluded: true })
  })

  it('pages the passages by 50 and previews 3000 characters first', async () => {
    const many = passages(60)
    server.use(
      http.get('/api/document-versions/:id/passages', ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get('offset'))
        return HttpResponse.json({ total: 60, items: many.slice(offset, offset + 50) })
      }),
    )
    setupWorld({ documents: [doc()] })
    server.use(
      http.get('/api/document-versions/:id/passages', ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get('offset'))
        return HttpResponse.json({ total: 60, items: many.slice(offset, offset + 50) })
      }),
    )
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /Open Ateny/ }))
    expect(await screen.findByText('Showing 50 of 60')).toBeInTheDocument()
    expect(document.querySelector('.text-preview')!.textContent).toHaveLength(3000)
    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))
    expect(document.querySelector('.text-preview')!.textContent).toHaveLength(3500)
    await userEvent.click(screen.getByRole('button', { name: /Show more passages/ }))
    expect(await screen.findByText('Showing 60 of 60')).toBeInTheDocument()
  })
})

describe('SubjectPage: topics', () => {
  it('links to the topic editor once topics exist', async () => {
    setupWorld({ topics: [topic()] })
    renderPage()
    expect(await screen.findByRole('link', { name: 'Edit topics' })).toHaveAttribute(
      'href',
      '/library/1/topics',
    )
  })

  it('has no editor link without topics', async () => {
    setupWorld()
    renderPage()
    await screen.findByRole('heading', { name: 'Historia' })
    expect(screen.queryByRole('link', { name: 'Edit topics' })).toBeNull()
  })
})

describe('SubjectPage: keyboard', () => {
  it('reaches every enabled control with the tab key', async () => {
    setupWorld({
      documents: [doc()],
      topics: [{ id: 5, title: 'Ustrój', description: null, origin: 'ai', status: 'active', objectives: [], passage_count: 3, question_count: 0 }],
      jobs: [job({ status: 'failed', resumable: true })],
    })
    renderPage()
    await screen.findByRole('button', { name: /Open Ateny/ })
    const controls = [
      ...screen.getAllByRole('button'),
      ...screen.getAllByRole('link'),
      ...screen.getAllByRole('spinbutton'),
      ...screen.getAllByRole('combobox'),
    ].filter((element) => !element.hasAttribute('disabled'))
    const reached = new Set<Element>()
    for (let i = 0; i < controls.length + 5; i++) {
      await userEvent.tab()
      if (document.activeElement) reached.add(document.activeElement)
    }
    for (const control of controls) expect(reached.has(control)).toBe(true)
  })
})

describe('SubjectPage: jobs', () => {
  it('starts extraction and follows the progress bar', async () => {
    const world = setupWorld({
      documents: [doc()],
      jobPolls: [
        job({ steps_done: 1 }),
        job({ steps_done: 3 }),
        job({ status: 'done', steps_done: 4, usage: [{ provider: 'openai', model: 'gpt-x', calls: 4, errors: 0, input_tokens: 1200, output_tokens: 300 }] }),
      ],
    })
    world.jobs = []
    renderPage()
    const button = await screen.findByRole('button', { name: /Extract topics/ })
    await userEvent.click(button)
    expect(texts(world, 'POST', /topics\/extract/)[0].body).toEqual({ document_version_id: 10 })
    const bar = await screen.findByRole('progressbar', { name: 'Extracting topics' })
    await waitFor(() => expect(bar).toHaveAttribute('aria-valuenow', '3'))
    expect(await screen.findByText('done')).toBeInTheDocument()
    expect(bar).toHaveAttribute('aria-valuenow', '4')
    expect(
      screen.getByText('Usage: 4 calls, 1200 input tokens, 300 output tokens (openai/gpt-x)'),
    ).toBeInTheDocument()
  })

  it('disables extraction while a job for the document is live', async () => {
    setupWorld({ documents: [doc()], jobs: [job()], jobPolls: [job()] })
    renderPage()
    expect(await screen.findByRole('button', { name: /Extract topics/ })).toBeDisabled()
  })

  it('re-attaches to a live job on reload', async () => {
    setupWorld({
      documents: [doc()],
      jobs: [job({ steps_done: 1 })],
      jobPolls: [job({ steps_done: 2 }), job({ steps_done: 3 })],
    })
    renderPage()
    const bar = await screen.findByRole('progressbar', { name: 'Extracting topics' })
    // The first poll may already have landed under load; what matters is that it re-attached.
    expect(['1', '2']).toContain(bar.getAttribute('aria-valuenow'))
    await waitFor(() => expect(bar).toHaveAttribute('aria-valuenow', '3'))
    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('offers "Resume" for a failed job and resumes it', async () => {
    const world = setupWorld({
      documents: [doc()],
      jobs: [job({ status: 'failed', steps_done: 2, error_message: 'The provider did not respond in time.', resumable: true })],
      jobPolls: [job({ status: 'queued', steps_done: 2 }), job({ status: 'running', steps_done: 3 })],
    })
    renderPage()
    expect(await screen.findByText('The provider did not respond in time.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }))
    expect(texts(world, 'POST', /jobs\/7\/resume/)).toHaveLength(1)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull())
    await waitFor(() => expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '3'))
  })

  it('shows a job cut off by a restart as resumable', async () => {
    setupWorld({ jobs: [job({ status: 'running', resumable: true })], jobPolls: [job({ status: 'running', resumable: true })] })
    renderPage()
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeInTheDocument()
  })

  it('says the usage is unknown after a restart', async () => {
    setupWorld({ jobs: [job({ status: 'done', steps_done: 4, usage: [] })] })
    renderPage()
    expect(
      await screen.findByText('Usage unknown (the app was restarted)'),
    ).toBeInTheDocument()
  })

  it('generates questions and refreshes the count when the job ends', async () => {
    const world = setupWorld({
      topics: [{ id: 5, title: 'Ustrój', description: null, origin: 'ai', status: 'active', objectives: [], passage_count: 3, question_count: 0 }],
      jobPolls: [job({ id: 8, kind: 'questions.generate', status: 'done', steps_done: 4 })],
    })
    renderPage()
    expect(await screen.findByText('Eklezja', { exact: false })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review questions (0)' })).toBeInTheDocument()
    world.questionCount = 6 // what the bank holds once the job is done
    await userEvent.click(screen.getByRole('button', { name: 'Generate questions' }))
    expect(texts(world, 'POST', /questions\/generate/)[0].body).toEqual({
      subject_id: 1,
      per_topic: 2,
      style: 'mixed',
      difficulty: 'mixed',
    })
    expect(await screen.findByRole('link', { name: 'Review questions (6)' }, { timeout: 3000 })).toHaveAttribute(
      'href',
      '/library/1/questions',
    )
  })

  it('shows the backend message when a batch with another source policy is running', async () => {
    setupWorld({
      topics: [{ id: 5, title: 'Ustrój', description: null, origin: 'ai', status: 'active', objectives: [], passage_count: 3, question_count: 0 }],
    })
    const detail = 'A question batch with a different source policy is already running. Wait for it to finish.'
    server.use(http.post('/api/questions/generate', () => HttpResponse.json({ detail }, { status: 409 })))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Generate questions' }))
    expect(await screen.findByText(detail)).toBeInTheDocument()
  })

  it('keeps generation disabled until topics exist', async () => {
    setupWorld()
    renderPage()
    expect(await screen.findByRole('button', { name: 'Generate questions' })).toBeDisabled()
    expect(screen.getByText('Extract topics first.')).toBeInTheDocument()
  })

  it('disables the job buttons without a key and links to the settings', async () => {
    setupWorld({
      hasKey: false,
      documents: [doc()],
      topics: [{ id: 5, title: 'Ustrój', description: null, origin: 'ai', status: 'active', objectives: [], passage_count: 3, question_count: 0 }],
    })
    renderPage()
    expect(await screen.findByText(/First add an API key in Settings\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings')
    expect(screen.getByRole('button', { name: /Extract topics/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Generate questions' })).toBeDisabled()
  })
})

describe('SubjectPage: deleting', () => {
  it('requires typing the subject name', async () => {
    const world = setupWorld()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete subject' }))
    expect(await screen.findByText(/This removes, permanently:/)).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: 'Delete permanently' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Type the subject name/), 'Histor')
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Type the subject name/), 'ia')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    expect(await screen.findByText('Subject list')).toBeInTheDocument()
    expect(texts(world, 'DELETE', /subjects\/1$/)).toHaveLength(1)
  })

  it('Escape closes the dialog even when the focus has left it', async () => {
    const world = setupWorld()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete subject' }))
    const dialog = await screen.findByRole('dialog')
    ;(document.activeElement as HTMLElement | null)?.blur()
    expect(document.activeElement).toBe(document.body)
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(dialog).not.toBeInTheDocument()
    expect(texts(world, 'DELETE', /subjects\/1$/)).toHaveLength(0)
  })

  it('shows what will be removed, exactly, and sends the fingerprint', async () => {
    const world = setupWorld()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete subject' }))
    const dialog = await screen.findByRole('dialog')
    const rows = within(dialog)
      .getAllByRole('row')
      .map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))
    expect(rows).toEqual([
      ['Documents', '2 (3 versions, 40 passages)'],
      ['Topics and learning objectives', '4 topics, 9 objectives'],
      ['Questions', '12 (15 versions with edits)'],
      ['Rounds and answers', '3 rounds, 20 answers'],
      ['Recordings', '18 (2.1 MB)'],
      ['Coverage and trends for this subject', 'all'],
    ])
    expect(within(dialog).getByText('Usage records (costs) stay, without the subject name.')).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: 'Export history' })).toHaveAttribute('href', '/settings#storage')
    expect(within(dialog).getByRole('link', { name: 'make a backup' })).toHaveAttribute('href', '/settings#storage')
    await userEvent.type(within(dialog).getByLabelText(/Type the subject name/), 'Historia')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))
    await screen.findByText('Subject list')
    const [sent] = texts(world, 'DELETE', /subjects\/1$/)
    expect(sent.search).toBe('?confirm=true&fingerprint=fp-1')
  })

  it('uses the singular for one of each', async () => {
    setupWorld({
      deletion: deletionPreview({
        documents: 1,
        versions: 1,
        passages: 1,
        topics: 1,
        objectives: 1,
        questions: 1,
        question_versions: 1,
        rounds: 1,
        answers: 1,
        recordings: 0,
        recording_bytes: 0,
      }),
    })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete subject' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('1 (1 version, 1 passage)')).toBeInTheDocument()
    expect(within(dialog).getByText('1 topic, 1 objective')).toBeInTheDocument()
    expect(within(dialog).getByText('1 (1 version with edits)')).toBeInTheDocument()
    expect(within(dialog).getByText('1 round, 1 answer')).toBeInTheDocument()
    expect(within(dialog).getByText('0 (0 B)')).toBeInTheDocument()
  })

  it('refreshes the table and clears the typed name after a 409', async () => {
    const world = setupWorld({
      deletionChanged: deletionPreview({ answers: 21, rounds: 4, fingerprint: 'fp-2' }),
    })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete subject' }))
    const dialog = await screen.findByRole('dialog')
    const input = within(dialog).getByLabelText(/Type the subject name/)
    await userEvent.type(input, 'Historia')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))
    expect(await within(dialog).findByText('4 rounds, 21 answers')).toBeInTheDocument()
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'This subject changed since you looked. Check the new summary.',
    )
    expect(input).toHaveValue('')
    expect(within(dialog).getByRole('button', { name: 'Delete permanently' })).toBeDisabled()
    expect(screen.queryByText('Subject list')).toBeNull()
    await userEvent.type(input, 'Historia')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))
    await screen.findByText('Subject list')
    const sent = texts(world, 'DELETE', /subjects\/1$/).map((r) => r.search)
    expect(sent).toEqual(['?confirm=true&fingerprint=fp-1', '?confirm=true&fingerprint=fp-2'])
  })
})
