import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { languagesWith } from '../library/languages.mocks'
import { requestsTo, setupQuick } from './quick.mocks'
import { QuickUploadPage } from './QuickUploadPage'

function renderUpload() {
  return render(
    <MemoryRouter initialEntries={['/quick']}>
      <Routes>
        <Route path="/quick" element={<QuickUploadPage />} />
        <Route path="*" element={<p>next</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const txt = () => new File(['Treść pliku.'], 'Biologia.txt', { type: 'text/plain' })

describe('QuickUploadPage content language', () => {
  it('uses the only enabled language without asking', async () => {
    const world = setupQuick()
    renderUpload()
    await userEvent.upload(screen.getByLabelText('Choose a file'), txt())
    const button = screen.getByRole('button', { name: /^Upload „/ })
    await waitFor(() => expect(button).toBeEnabled())
    expect(screen.queryByLabelText('Content language')).toBeNull()
    await userEvent.click(button)
    await screen.findByText('next')
    expect(requestsTo(world, 'POST', /^\/api\/subjects$/)[0].body).toMatchObject({ language: 'pl' })
  })

  it('asks for the language when several are enabled', async () => {
    const world = setupQuick()
    server.use(http.get('/api/languages', () => HttpResponse.json(languagesWith('en'))))
    renderUpload()
    const select = await screen.findByRole('combobox', { name: 'Content language' })
    await userEvent.upload(screen.getByLabelText('Choose a file'), txt())
    const button = screen.getByRole('button', { name: /^Upload „/ })
    expect(select).toHaveValue('')
    expect(button).toBeDisabled()
    await userEvent.selectOptions(select, 'English')
    expect(button).toBeEnabled()
    await userEvent.click(button)
    await screen.findByText('next')
    expect(requestsTo(world, 'POST', /^\/api\/subjects$/)[0].body).toMatchObject({ language: 'en' })
  })

  it('says so when the languages cannot be loaded, and creates nothing', async () => {
    const world = setupQuick()
    server.use(http.get('/api/languages', () => HttpResponse.json({ detail: 'x' }, { status: 500 })))
    renderUpload()
    expect(await screen.findByText('Could not load the languages.')).toBeInTheDocument()
    await userEvent.upload(screen.getByLabelText('Choose a file'), txt())
    expect(screen.getByRole('button', { name: /^Upload „/ })).toBeDisabled()
    expect(requestsTo(world, 'POST', /^\/api\/subjects$/)).toHaveLength(0)
  })
})
