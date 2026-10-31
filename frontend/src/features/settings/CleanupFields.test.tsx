import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { cleanupText } from './CleanupFields'
import { StorageSection } from './StorageSection'
import { changesOf, storageBackend } from './storage.mocks'

const subjects = [{ id: 1, name: 'Demokracja ateńska', description: null, document_count: 2 }]

function backend(patch: Parameters<typeof storageBackend>[0] = {}) {
  const world = storageBackend(patch)
  server.use(http.get('/api/subjects', () => HttpResponse.json(subjects)))
  return world
}

async function renderSection() {
  const view = render(<StorageSection onRestored={() => undefined} />)
  await screen.findByRole('heading', { level: 3, name: 'Automatic clean-up' })
  return view
}

const keepField = () => screen.getByLabelText('Pre-restore backups to keep')
const daysField = () => screen.getByLabelText('Delete exports after (days)')
const saveButton = () => screen.getByRole('button', { name: 'Save clean-up' })

async function setValues(keep: string, days: string) {
  await userEvent.clear(keepField())
  if (keep) await userEvent.type(keepField(), keep)
  await userEvent.clear(daysField())
  if (days) await userEvent.type(daysField(), days)
}

describe('CleanupFields', () => {
  it('shows the limits and is disabled until a value changes', async () => {
    backend()
    await renderSection()
    expect(keepField()).toHaveValue(5)
    expect(daysField()).toHaveValue(30)
    expect(keepField()).toHaveAttribute('min', '0')
    expect(keepField()).toHaveAttribute('max', '100')
    expect(daysField()).toHaveAttribute('max', '3650')
    expect(saveButton()).toBeDisabled()
    expect(
      screen.getByText('The newest are kept; 0 keeps all. Backups you make yourself are never deleted automatically.'),
    ).toBeInTheDocument()
    expect(screen.getByText('0 keeps all exports.')).toBeInTheDocument()
  })

  it('saves both limits and says what was deleted', async () => {
    const world = backend({ cleanup: { deleted_backups: 2, deleted_exports: 1 } })
    await renderSection()
    await setValues('3', '14')
    expect(saveButton()).toBeEnabled()
    await userEvent.click(saveButton())
    const status = await screen.findByText('Clean-up saved. Deleted 2 old pre-restore backups and 1 export.')
    expect(status.closest('[role="status"]')).not.toBeNull()
    expect(changesOf(world)).toEqual([
      { method: 'PUT', path: '/api/data/cleanup', body: { pre_restore_keep: 3, export_keep_days: 14 } },
    ])
    await waitFor(() => expect(keepField()).toHaveValue(3))
    expect(daysField()).toHaveValue(14)
    expect(saveButton()).toBeDisabled()
  })

  it('says only "Clean-up saved." when nothing was deleted', async () => {
    backend()
    await renderSection()
    await setValues('3', '14')
    await userEvent.click(saveButton())
    const status = await screen.findByText('Clean-up saved.')
    expect(status.closest('[role="status"]')).not.toBeNull()
  })

  it.each([
    ['-1', '30'],
    ['101', '30'],
    ['5', '3651'],
    ['', '30'],
    ['5', ''],
  ])('keeps Save disabled for %s / %s', async (keep, days) => {
    const world = backend()
    await renderSection()
    await setValues(keep, days)
    expect(saveButton()).toBeDisabled()
    await userEvent.click(saveButton())
    expect(changesOf(world)).toEqual([])
  })

  it('shows a refusal in the error notice', async () => {
    backend({ cleanupError: 'Enter a number of backups from 0 to 100.' })
    await renderSection()
    await setValues('3', '30')
    await userEvent.click(saveButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter a number of backups from 0 to 100.')
  })

  it('sits under the export block and leaves "Keep recordings" to the retention select', async () => {
    backend()
    const { container } = await renderSection()
    const headings = within(container.querySelector('section#storage') as HTMLElement)
      .getAllByRole('heading', { level: 3 })
      .map((h) => h.textContent)
    expect(headings).toEqual(['Backup and restore', 'Export', 'Automatic clean-up', 'Recordings'])
    expect(screen.getByLabelText('Keep recordings').tagName).toBe('SELECT')
  })
})

describe('cleanupText', () => {
  const base = { pre_restore_keep: 5, export_keep_days: 30 }
  it('names what was deleted', () => {
    expect(cleanupText({ ...base, deleted_backups: 0, deleted_exports: 0 })).toBe('Clean-up saved.')
    expect(cleanupText({ ...base, deleted_backups: 1, deleted_exports: 0 })).toBe(
      'Clean-up saved. Deleted 1 old pre-restore backup.',
    )
    expect(cleanupText({ ...base, deleted_backups: 0, deleted_exports: 3 })).toBe('Clean-up saved. Deleted 3 exports.')
  })
})
