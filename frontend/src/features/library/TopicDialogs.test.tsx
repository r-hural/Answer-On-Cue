import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
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

describe('Merge and split dialogs', () => {
  it('merges at least two ticked topics, in list order', async () => {
    const world = setupWorld({ topics: two() })
    renderPage()
    const user = userEvent.setup()
    await screen.findByDisplayValue('Ustrój')
    const merge = screen.getByRole('button', { name: /Merge selected/ })
    expect(merge).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Select topic: Wojny' }))
    expect(merge).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Select topic: Ustrój' }))
    expect(merge).toBeEnabled()
    expect(merge).toHaveTextContent('(2)')
    await user.click(merge)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('2 objectives, 2 questions')).toBeInTheDocument()
    expect(within(dialog).getByText('1 objective, 1 question')).toBeInTheDocument()
    const name = within(dialog).getByLabelText('Name of the merged topic')
    expect(name).toHaveValue('Ustrój')
    expect(name).toHaveFocus()
    await user.clear(name)
    await user.type(name, 'Grecja')
    await user.click(within(dialog).getByRole('button', { name: 'Merge' }))
    expect(await screen.findByText('Merged into „Grecja”.')).toBeInTheDocument()
    expect(sent(world, 'POST', /merge/)[0].body).toEqual({ topic_ids: [5, 6], title: 'Grecja' })
    expect(await screen.findByDisplayValue('Grecja')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Wojny')).toBeNull()
  })

  it('splits a topic with every objective in exactly one part', async () => {
    const world = setupWorld({ topics: two() })
    renderPage()
    const user = userEvent.setup()
    await screen.findByDisplayValue('Ustrój')
    const splits = screen.getAllByRole('button', { name: 'Split' })
    await user.click(splits[0])
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Ustrój')).toBeInTheDocument()
    const first = within(dialog).getByLabelText('New topic 1')
    expect(first).toHaveFocus()
    const submit = within(dialog).getByRole('button', { name: 'Split' })
    expect(submit).toBeDisabled()
    await user.type(first, 'Organy')
    expect(submit).toBeDisabled()
    await user.type(within(dialog).getByLabelText('New topic 2'), 'Rada')
    expect(submit).toBeEnabled()
    const second = within(dialog).getByLabelText('New topic for: Wyjaśnić rolę rady')
    expect(second).toHaveDisplayValue('Rada')
    expect(within(dialog).getByLabelText('New topic for: Opisać ustrój')).toHaveDisplayValue('Organy')
    await user.click(submit)
    expect(
      await screen.findByText('Split into 2 topics; their questions are marked for review.'),
    ).toBeInTheDocument()
    expect(sent(world, 'POST', /split/)[0].body).toEqual({
      parts: [
        { title: 'Organy', objective_ids: [50], passage_ids: [] },
        { title: 'Rada', objective_ids: [51], passage_ids: [] },
      ],
    })
    expect(await screen.findByDisplayValue('Organy')).toBeInTheDocument()
  })

  it('cannot split a topic with one objective, and Escape closes the dialog', async () => {
    setupWorld({ topics: two() })
    renderPage()
    const user = userEvent.setup()
    await screen.findByDisplayValue('Wojny')
    await user.click(screen.getAllByRole('button', { name: 'Split' })[1])
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('New topic 1'), 'A')
    await user.type(within(dialog).getByLabelText('New topic 2'), 'B')
    expect(within(dialog).getByRole('button', { name: 'Split' })).toBeDisabled()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('cannot split when a part would get no objective, and says why', async () => {
    setupWorld({ topics: two() })
    renderPage()
    const user = userEvent.setup()
    await screen.findByDisplayValue('Ustrój')
    await user.click(screen.getAllByRole('button', { name: 'Split' })[0])
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('New topic 1'), 'A')
    await user.type(within(dialog).getByLabelText('New topic 2'), 'B')
    const submit = within(dialog).getByRole('button', { name: 'Split' })
    expect(submit).toBeEnabled()
    const need = 'Each new topic needs at least one learning objective.'
    expect(within(dialog).queryByText(need)).toBeNull()
    await user.selectOptions(within(dialog).getByLabelText('New topic for: Wyjaśnić rolę rady'), 'A')
    expect(submit).toBeDisabled()
    expect(within(dialog).getByText(need)).toBeInTheDocument()
  })
})

describe('Topic dialog as a modal', () => {
  it('keeps Tab inside, closes on Escape from anywhere and gives focus back', async () => {
    setupWorld({ topics: two() })
    renderPage()
    const user = userEvent.setup()
    await screen.findByDisplayValue('Ustrój')
    const trigger = screen.getAllByRole('button', { name: 'Remove' })[0]
    await user.click(trigger)
    const dialog = screen.getByRole('dialog')
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    const confirm = within(dialog).getByRole('button', { name: 'Remove' })
    expect(cancel).toHaveFocus()
    await user.tab({ shift: true })
    expect(confirm).toHaveFocus()
    await user.tab()
    expect(cancel).toHaveFocus()
    ;(document.activeElement as HTMLElement).blur()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveFocus()
  })
})
