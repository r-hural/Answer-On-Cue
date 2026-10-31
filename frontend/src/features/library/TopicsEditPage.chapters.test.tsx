import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { chapteredWorld } from './library.mocks'
import { TopicsEditPage } from './TopicsEditPage'

function renderPage(entry = '/library/1/topics') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/library/:subjectId/topics" element={<TopicsEditPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const names = () => screen.getAllByRole('textbox', { name: 'Topic name' }).map((box) => (box as HTMLTextAreaElement).value)

describe('TopicsEditPage with two chapters', () => {
  it('shows chapter tabs and only the chapter’s topics', async () => {
    chapteredWorld()
    renderPage()
    const tabs = await screen.findByRole('navigation', { name: 'Chapters' })
    expect(within(tabs).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Unnamed chapter',
      'Rewolucja francuska',
    ])
    await waitFor(() => expect(names()).toEqual(['Ustrój', 'Wojny perskie']))
    expect(screen.getByRole('link', { name: 'Done' })).toHaveAttribute('href', '/library/1?chapter=1')
    await userEvent.click(within(tabs).getByRole('link', { name: 'Rewolucja francuska' }))
    await waitFor(() => expect(names()).toEqual(['Terror']))
  })

  it('merges only within the chapter and shows a refused merge', async () => {
    chapteredWorld()
    const detail = 'Topics from different chapters cannot be merged. Move one first.'
    server.use(http.post('/api/topics/merge', () => HttpResponse.json({ detail }, { status: 422 })))
    renderPage('/library/1/topics?chapter=1')
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Select topic: Ustrój' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select topic: Wojny perskie' }))
    expect(screen.queryByRole('checkbox', { name: 'Select topic: Terror' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Merge selected/ }))
    const dialog = screen.getByRole('dialog', { name: 'Merge 2 topics' })
    expect(within(dialog).getAllByRole('listitem')).toHaveLength(2)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Merge' }))
    expect(await screen.findByText(detail)).toBeInTheDocument()
  })
})
