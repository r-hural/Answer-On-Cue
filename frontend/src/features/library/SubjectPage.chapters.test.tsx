import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { chapter, chapteredWorld, doc, setupWorld, type World } from './library.mocks'
import { SubjectPage } from './SubjectPage'

function renderPage(entry = '/library/1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/library/:subjectId" element={<SubjectPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** Every request URL the page makes, GETs included. */
let urls: string[] = []
beforeEach(() => {
  urls = []
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url)
    urls.push(`${request.method} ${url.pathname}${url.search}`)
  })
})
afterEach(() => server.events.removeAllListeners())

const posts = (world: World, path: RegExp) => world.requests.filter((r) => r.method === 'POST' && path.test(r.path))

function drop(file = new File(['abc'], 'nowy.txt', { type: 'text/plain' })) {
  fireEvent.drop(screen.getByRole('button', { name: /Drop a PDF/ }), { dataTransfer: { files: [file] } })
}

describe('SubjectPage with one chapter', () => {
  it('shows no tabs and never asks for chapters', async () => {
    setupWorld({ documents: [doc()] })
    renderPage()
    await screen.findByRole('button', { name: 'Move Ateny' })
    await waitFor(() => expect(urls.some((u) => u.startsWith('GET /api/topics'))).toBe(true))
    expect(screen.queryByRole('navigation', { name: 'Chapters' })).toBeNull()
    expect(screen.queryByText('Name the first chapter so you can tell them apart.')).toBeNull()
    expect(urls.filter((u) => /chapters|mixed-topics|chapter_id/.test(u))).toEqual([])
  })

  it('uploads without chapter fields when the field is empty, and with the typed name', async () => {
    const world = setupWorld()
    renderPage()
    const field = await screen.findByLabelText('Chapter (optional)')
    expect(field).toHaveValue('')
    expect(field).not.toHaveAttribute('lang')
    drop()
    await waitFor(() => expect(posts(world, /\/subjects\/1\/documents$/)).toHaveLength(1))
    expect(String(posts(world, /documents$/)[0].body)).not.toContain('chapter_')

    await userEvent.type(field, 'Rewolucja francuska')
    drop()
    await waitFor(() => expect(posts(world, /\/subjects\/1\/documents$/)).toHaveLength(2))
    const body = String(posts(world, /documents$/)[1].body)
    expect(body).toContain('name="chapter_name"')
    expect(body).toContain('Rewolucja francuska')
    expect(body).not.toContain('name="chapter_id"')
  })

  it('moves a document only to a new chapter', async () => {
    const world = setupWorld({ documents: [doc()], topicsMoved: [5] })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Move Ateny' }))
    const dialog = screen.getByRole('dialog', { name: 'Move to a chapter' })
    expect(within(dialog).getAllByRole('radio')).toHaveLength(1)
    expect(within(dialog).getByRole('radio', { name: 'New chapter' })).toBeChecked()
    expect(within(dialog).getByRole('button', { name: 'Move' })).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Name of the new chapter'), 'Grecja')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move' }))
    expect(await screen.findByText('Moved, with 1 topic.')).toBeInTheDocument()
    expect(posts(world, /\/documents\/1\/chapter$/)[0].body).toEqual({ new_chapter_name: 'Grecja' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('SubjectPage with two chapters', () => {
  it('shows a tab per chapter; the first one is unnamed and English', async () => {
    chapteredWorld()
    renderPage()
    const tabs = await screen.findByRole('navigation', { name: 'Chapters' })
    expect(tabs).toHaveClass('tabs')
    const links = within(tabs).getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual(['Unnamed chapter', 'Rewolucja francuska'])
    expect(links[0].querySelector('[lang]')).toBeNull()
    expect(links[1].querySelector('[lang]')).toBeNull()
    expect(links[1]).toHaveTextContent('Rewolucja francuska')
    expect(links[0]).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('Name the first chapter so you can tell them apart.')).toBeInTheDocument()
  })

  it('documents, topics, the question count and coverage follow the tab', async () => {
    chapteredWorld()
    renderPage()
    expect(await screen.findByRole('button', { name: 'Move Ateny' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Move Rewolucja' })).toBeNull()
    expect(await screen.findByRole('link', { name: 'Review questions (3)' })).toBeInTheDocument()
    expect(screen.getByText('Ustrój')).toBeInTheDocument()
    expect(screen.queryByText('Terror')).toBeNull()
    await waitFor(() => expect(urls).toContain('GET /api/coverage/subjects/1?layer=all&chapter_id=1'))
    expect(urls).toContain('GET /api/topics?subject_id=1&chapter_id=1')
    expect(urls).toContain('GET /api/questions?subject_id=1&status=active&status=needs_review&chapter_id=1')
    expect(screen.getByLabelText('Chapter (optional)')).toHaveValue('')

    await userEvent.click(screen.getByRole('link', { name: 'Rewolucja francuska' }))
    expect(await screen.findByRole('button', { name: 'Move Rewolucja' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Move Ateny' })).toBeNull()
    expect(await screen.findByText('Terror')).toBeInTheDocument()
    expect(screen.queryByText('Ustrój')).toBeNull()
    expect(await screen.findByRole('link', { name: 'Review questions (0)' })).toBeInTheDocument()
    await waitFor(() => expect(urls).toContain('GET /api/coverage/subjects/1?layer=all&chapter_id=2'))
    expect(screen.getByLabelText('Chapter (optional)')).toHaveValue('Rewolucja francuska')
    expect(screen.getByRole('link', { name: 'Rewolucja francuska' })).toHaveAttribute('aria-current', 'page')
  })

  it('selects the tab from ?chapter= and uploads into it by name', async () => {
    const world = chapteredWorld()
    renderPage('/library/1?chapter=2')
    expect(await screen.findByRole('button', { name: 'Move Rewolucja' })).toBeInTheDocument()
    const field = screen.getByLabelText('Chapter (optional)')
    expect(field).toHaveValue('Rewolucja francuska')
    expect(screen.getByText('Leave empty to add it to', { exact: false })).toHaveTextContent(
      'Leave empty to add it to Unnamed chapter.',
    )
    drop()
    await waitFor(() => expect(posts(world, /\/subjects\/1\/documents$/)).toHaveLength(1))
    expect(String(posts(world, /documents$/)[0].body)).toContain('name="chapter_name"')
  })

  it('moves a document to another chapter and reloads', async () => {
    const world = chapteredWorld({ topicsMoved: [5, 6] })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Move Ateny' }))
    const dialog = screen.getByRole('dialog', { name: 'Move to a chapter' })
    expect(dialog.querySelectorAll('.btn.primary')).toHaveLength(1)
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    const target = within(dialog).getByRole('radio', { name: 'Rewolucja francuska' })
    expect(target).toBeChecked()
    expect(within(dialog).queryByRole('radio', { name: 'Unnamed chapter' })).toBeNull()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move' }))
    expect(await screen.findByText('Moved, with 2 topics.')).toBeInTheDocument()
    expect(posts(world, /\/documents\/1\/chapter$/)[0].body).toEqual({ chapter_id: 2 })
    // the reloaded list: Ateny is now in the other chapter
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Move Ateny' })).toBeNull())
  })

  it('moves to a new chapter by name', async () => {
    const world = chapteredWorld()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Move Ateny' }))
    const dialog = screen.getByRole('dialog', { name: 'Move to a chapter' })
    await userEvent.click(within(dialog).getByRole('radio', { name: 'New chapter' }))
    await userEvent.type(within(dialog).getByLabelText('Name of the new chapter'), 'Starożytna Grecja')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move' }))
    expect(await screen.findByText('Moved. No topics moved with it.')).toBeInTheDocument()
    expect(posts(world, /\/documents\/1\/chapter$/)[0].body).toEqual({ new_chapter_name: 'Starożytna Grecja' })
    expect(await screen.findByRole('link', { name: 'Starożytna Grecja' })).toBeInTheDocument()
  })

  it('shows a refused move in the dialog', async () => {
    const message = 'Wait for the topic or question work of this subject to finish first.'
    chapteredWorld({ moveError: message })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Move Ateny' }))
    const dialog = screen.getByRole('dialog', { name: 'Move to a chapter' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(message)
  })

  it('renames the current chapter', async () => {
    const world = chapteredWorld()
    renderPage('/library/1?chapter=1')
    await userEvent.click(await screen.findByRole('button', { name: 'Rename chapter' }))
    const field = screen.getByLabelText('Chapter name')
    await userEvent.type(field, 'Starożytna Grecja')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(world.requests.find((r) => r.method === 'PATCH' && r.path === '/api/chapters/1')?.body).toEqual({
        name: 'Starożytna Grecja',
      }),
    )
    expect(await screen.findByRole('link', { name: 'Starożytna Grecja' })).toBeInTheDocument()
    expect(screen.queryByText('Name the first chapter so you can tell them apart.')).toBeNull()
  })

  it('offers Delete only on an empty chapter that is not the first', async () => {
    const empty = chapter({ id: 3, name: 'Pusty', is_default: false, ordinal: 2, document_count: 0, topic_count: 0, question_count: 0 })
    const world = chapteredWorld()
    world.chapters = [...world.chapters!, empty]
    const view = renderPage('/library/1?chapter=1')
    await screen.findByRole('button', { name: 'Rename chapter' })
    expect(screen.queryByRole('button', { name: 'Delete chapter' })).toBeNull()
    view.unmount()
    const second = renderPage('/library/1?chapter=2')
    await screen.findByRole('button', { name: 'Move Rewolucja' })
    expect(screen.queryByRole('button', { name: 'Delete chapter' })).toBeNull()
    second.unmount()
    renderPage('/library/1?chapter=3')
    await userEvent.click(await screen.findByRole('button', { name: 'Delete chapter' }))
    await waitFor(() => expect(world.requests.some((r) => r.method === 'DELETE' && r.path === '/api/chapters/3')).toBe(true))
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Pusty' })).toBeNull())
  })
})

describe('SubjectPage chapter switch and rename focus', () => {
  it('hides the previous chapter’s topics and disables Generate until the new chapter’s topics arrive', async () => {
    const world = chapteredWorld()
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.get('/api/topics', async ({ request }) => {
        const id = Number(new URL(request.url).searchParams.get('chapter_id'))
        if (id === 2) await gate
        return HttpResponse.json(world.topics.filter((t) => t.chapter_id === id))
      }),
    )
    renderPage('/library/1?chapter=1')
    expect(await screen.findByText('Ustrój')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate questions' })).toBeEnabled())
    await userEvent.click(screen.getByRole('link', { name: 'Rewolucja francuska' }))
    expect(await screen.findByRole('button', { name: 'Move Rewolucja' })).toBeInTheDocument()
    expect(screen.queryByText('Ustrój')).toBeNull()
    expect(screen.getByRole('button', { name: 'Generate questions' })).toBeDisabled()
    release()
    expect(await screen.findByText('Terror')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate questions' })).toBeEnabled())
  })

  it('focuses the name field on Rename and returns focus to the button on Cancel and after Save', async () => {
    chapteredWorld()
    renderPage('/library/1?chapter=2')
    await userEvent.click(await screen.findByRole('button', { name: 'Rename chapter' }))
    expect(screen.getByLabelText('Chapter name')).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Rename chapter' })).toHaveFocus()

    await userEvent.click(screen.getByRole('button', { name: 'Rename chapter' }))
    const field = screen.getByLabelText('Chapter name')
    expect(field).toHaveFocus()
    await userEvent.clear(field)
    await userEvent.type(field, 'Wielka rewolucja')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rename chapter' })).toHaveFocus())
  })
})
