import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setupWorld, topic, type World } from './library.mocks'
import { TopicsEditPage } from './TopicsEditPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/library/1/topics']}>
      <Routes>
        <Route path="/library/1" element={<p>Subject page</p>} />
        <Route path="/library/:subjectId/topics" element={<TopicsEditPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function sent(world: World, method: string, path: RegExp) {
  return world.requests.filter((r) => r.method === method && path.test(r.path))
}

const two = () => [
  topic({ id: 5, title: 'Ustrój' }),
  topic({
    id: 6,
    title: 'Wojny',
    objectives: [{ id: 60, text: 'Opisać wojny' }],
    question_count: 1,
    passage_count: 1,
  }),
]

afterEach(() => {
  vi.useRealTimers()
})

describe('TopicsEditPage', () => {
  it('lists the topics with objectives, terms and passages', async () => {
    setupWorld({ topics: two() })
    renderPage()
    const name = await screen.findByDisplayValue('Ustrój')
    expect(name).toHaveAttribute('lang', 'pl-PL')
    expect(name.tagName).toBe('TEXTAREA')
    expect(screen.getByText('Opisać ustrój')).toBeInTheDocument()
    expect(screen.getByText('Eklezja')).toBeInTheDocument()
    expect(screen.getByText('3 passages')).toBeInTheDocument()
    expect(screen.getByText('2 objectives')).toBeInTheDocument()
    expect(screen.getByText(/2 topics, 3 learning objectives\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Done' })).toHaveAttribute('href', '/library/1')
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('saves a name 800 ms after the last key', async () => {
    const world = setupWorld({ topics: two() })
    renderPage()
    const name = await screen.findByDisplayValue('Ustrój')
    vi.useFakeTimers()
    fireEvent.change(name, { target: { value: 'Ust' } })
    await act(() => vi.advanceTimersByTimeAsync(500))
    fireEvent.change(name, { target: { value: 'Ustrój Aten ' } })
    expect(sent(world, 'PATCH', /topics/)).toHaveLength(0)
    await act(() => vi.advanceTimersByTimeAsync(790))
    expect(sent(world, 'PATCH', /topics/)).toHaveLength(0)
    await act(() => vi.advanceTimersByTimeAsync(20))
    vi.useRealTimers()
    await waitFor(() => expect(sent(world, 'PATCH', /topics\/5$/)).toHaveLength(1))
    expect(sent(world, 'PATCH', /topics\/5$/)[0].body).toEqual({ title: 'Ustrój Aten' })
  })

  it('saves a name typed just before leaving the page', async () => {
    const world = setupWorld({ topics: two() })
    const { unmount } = renderPage()
    const name = await screen.findByDisplayValue('Ustrój')
    fireEvent.change(name, { target: { value: 'Ustrój Aten' } })
    unmount()
    await waitFor(() => expect(sent(world, 'PATCH', /topics\/5$/)).toHaveLength(1))
    expect(sent(world, 'PATCH', /topics\/5$/)[0].body).toEqual({ title: 'Ustrój Aten' })
  })

  it('saves at once on blur and sends nothing for an empty or unchanged name', async () => {
    const world = setupWorld({ topics: two() })
    renderPage()
    const user = userEvent.setup()
    const name = await screen.findByDisplayValue('Ustrój')
    await user.type(name, '2')
    await user.tab()
    await waitFor(() => expect(sent(world, 'PATCH', /topics\/5$/)).toHaveLength(1))
    expect(sent(world, 'PATCH', /topics\/5$/)[0].body).toEqual({ title: 'Ustrój2' })
    const other = screen.getByDisplayValue('Wojny')
    await user.clear(other)
    await user.tab()
    expect(screen.getByDisplayValue('Wojny')).toBeInTheDocument()
    await user.click(other)
    await user.tab()
    expect(sent(world, 'PATCH', /topics\/6$/)).toHaveLength(0)
  })

  it('shows the server message when a name is refused', async () => {
    setupWorld({ topics: two(), topicError: 'The name must not be empty.' })
    renderPage()
    const user = userEvent.setup()
    await user.type(await screen.findByDisplayValue('Ustrój'), 'x')
    await user.tab()
    expect(await screen.findByRole('alert')).toHaveTextContent('The name must not be empty.')
  })

  it('removes a topic after a dialog that names the retired questions', async () => {
    const world = setupWorld({ topics: two() })
    renderPage()
    const user = userEvent.setup()
    await screen.findByDisplayValue('Ustrój')
    await user.click(screen.getAllByRole('button', { name: 'Remove' })[0])
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('2 questions will be retired.')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    await user.click(screen.getAllByRole('button', { name: 'Remove' })[0])
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(sent(world, 'DELETE', /topics/)).toHaveLength(0)
    await user.click(screen.getAllByRole('button', { name: 'Remove' })[0])
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }))
    expect(await screen.findByText('Removed „Ustrój”; 2 questions retired.')).toBeInTheDocument()
    expect(sent(world, 'DELETE', /topics\/5$/)).toHaveLength(1)
    expect(screen.queryByDisplayValue('Ustrój')).toBeNull()
  })

  it('shows the first 20 topics and the rest on request', async () => {
    const many = Array.from({ length: 25 }, (_, i) => topic({ id: 200 + i, title: `Temat ${i + 1}` }))
    setupWorld({ topics: many })
    renderPage()
    await screen.findByDisplayValue('Temat 1')
    expect(screen.getAllByLabelText('Topic name')).toHaveLength(20)
    await userEvent.click(screen.getByRole('button', { name: 'Show 5 more topics' }))
    expect(screen.getAllByLabelText('Topic name')).toHaveLength(25)
  })
})
