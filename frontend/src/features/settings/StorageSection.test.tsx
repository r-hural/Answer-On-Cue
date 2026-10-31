import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { StorageSection } from './StorageSection'
import {
  backupInfo,
  changesOf,
  exportInfo,
  preview,
  restorePreview,
  storageBackend,
  storageInfo,
} from './storage.mocks'

const subjects = [
  { id: 1, name: 'Demokracja ateńska', description: null, document_count: 2 },
  { id: 2, name: 'Biologia komórki', description: null, document_count: 1 },
]

function backend(patch: Parameters<typeof storageBackend>[0] = {}) {
  const world = storageBackend(patch)
  server.use(http.get('/api/subjects', () => HttpResponse.json(subjects)))
  return world
}

async function renderSection(onRestored = vi.fn()) {
  const view = render(<StorageSection onRestored={onRestored} />)
  await screen.findByRole('heading', { level: 2, name: 'Storage and backup' })
  await screen.findByText(/Data folder:/)
  return { ...view, onRestored }
}

const zipFile = () => new File(['zip'], 'answeroncue-backup-2026-10-15.zip', { type: 'application/zip' })

describe('StorageSection layout', () => {
  it('follows the settings template', async () => {
    backend()
    const { container } = await renderSection()
    const section = container.querySelector('section#storage') as HTMLElement
    expect(section).toHaveAttribute('aria-labelledby', 'h-storage')
    expect(within(section).getByText(/Data folder:/)).toHaveTextContent(
      'Data folder: C:\\Users\\user\\AppData\\Local\\AnswerOnCue\\data · 1.4 GB (recordings 1.1 GB)',
    )
    expect(section.querySelector('code')).toHaveTextContent('AnswerOnCue\\data')
    for (const name of ['Backup and restore', 'Export', 'Recordings'])
      expect(within(section).getByRole('heading', { level: 3, name })).toBeInTheDocument()
    expect(
      within(section).getByText(
        'A full backup holds subjects, document versions, question banks, rounds, answers and recordings. API keys are never included.',
      ),
    ).toBeInTheDocument()
    expect(
      within(section).getByText('History and transcripts as a readable file (for your notes or a teacher).'),
    ).toBeInTheDocument()
    expect(within(section).getByText('Transcripts and scores stay when a recording is deleted.')).toBeInTheDocument()
    for (const name of [
      'Make a backup',
      'Restore from a backup…',
      'Export history and transcripts',
      'Delete recordings now…',
    ])
      expect(within(section).getByRole('button', { name })).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: 'Delete recordings now…' })).toHaveClass('btn', 'danger')
    expect(section.querySelector('.btn.primary')).toBeNull()
  })

  it('shows the error when the details cannot be loaded', async () => {
    server.use(http.get('/api/data/storage', () => HttpResponse.json({ detail: 'x' }, { status: 500 })))
    render(<StorageSection />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the storage details.')
  })
})

describe('backup and export', () => {
  it('makes a backup and links the file for download', async () => {
    const world = backend()
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Make a backup' }))
    const notice = await screen.findByRole('status')
    expect(within(notice).getByText('Backup ready')).toBeInTheDocument()
    expect(notice).toHaveTextContent('(1.3 GB)')
    expect(notice).toHaveTextContent(
      'It does not contain your API keys: after a restore, enter them again.',
    )
    const link = within(notice).getByRole('link', { name: backupInfo.name })
    expect(link).toHaveAttribute('href', `/api/data/backups/${backupInfo.name}`)
    expect(link).toHaveAttribute('download', backupInfo.name)
    expect(changesOf(world).map((r) => [r.method, r.path])).toEqual([['POST', '/api/data/backups']])
  })

  it('exports the history and links the file', async () => {
    const world = backend()
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Export history and transcripts' }))
    const notice = await screen.findByRole('status')
    expect(within(notice).getByText('Export ready')).toBeInTheDocument()
    expect(within(notice).getByRole('link', { name: exportInfo.name })).toHaveAttribute(
      'href',
      `/api/data/exports/${exportInfo.name}`,
    )
    expect(changesOf(world).map((r) => r.path)).toEqual(['/api/data/exports'])
  })

  it('shows the server message when a backup fails', async () => {
    backend()
    server.use(
      http.post('/api/data/backups', () =>
        HttpResponse.json({ detail: 'There is not enough free disk space for this.' }, { status: 422 }),
      ),
    )
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Make a backup' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('There is not enough free disk space for this.')
  })
})

describe('keeping recordings', () => {
  it('offers the four policies and shows the stored one', async () => {
    backend({ info: { ...storageInfo, audio_retention: '90d' } })
    await renderSection()
    const select = screen.getByLabelText('Keep recordings') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      'Forever',
      'Until the answer is assessed',
      'For 30 days',
      'For 90 days',
    ])
    expect(select.value).toBe('90d')
  })

  it('asks with the exact numbers before deleting, then saves with the fingerprint', async () => {
    const world = backend()
    await renderSection()
    await userEvent.selectOptions(screen.getByLabelText('Keep recordings'), 'For 30 days')
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveTextContent('41 recordings, 0.8 GB will be deleted now')
    expect(changesOf(world)).toEqual([])
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete 41 recordings' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(changesOf(world)).toEqual([
      {
        method: 'PUT',
        path: '/api/data/retention',
        body: { policy: '30d', fingerprint: world.retention['30d'].fingerprint },
      },
    ])
    expect(await screen.findByText(/Recording retention saved\. Deleted 41 recordings/)).toBeInTheDocument()
    expect((screen.getByLabelText('Keep recordings') as HTMLSelectElement).value).toBe('30d')
  })

  it('cancelling the question changes nothing', async () => {
    const world = backend()
    await renderSection()
    await userEvent.selectOptions(screen.getByLabelText('Keep recordings'), 'For 30 days')
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(changesOf(world)).toEqual([])
    expect((screen.getByLabelText('Keep recordings') as HTMLSelectElement).value).toBe('forever')
  })

  it('applies at once when nothing would be deleted', async () => {
    const world = backend({ info: { ...storageInfo, audio_retention: '30d' } })
    await renderSection()
    await userEvent.selectOptions(screen.getByLabelText('Keep recordings'), 'Forever')
    await screen.findByText('Recording retention saved.')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(changesOf(world).map((r) => [r.method, (r.body as { policy: string }).policy])).toEqual([
      ['PUT', 'forever'],
    ])
  })

  it('shows the new numbers when the recordings changed meanwhile', async () => {
    const world = backend({ changed: preview(43, 900_000_000) })
    await renderSection()
    await userEvent.selectOptions(screen.getByLabelText('Keep recordings'), 'For 30 days')
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete 41 recordings' }))
    expect(await within(dialog).findByText('The recordings changed since you looked.')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('43 recordings, 0.8 GB will be deleted now')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete 43 recordings' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(changesOf(world).map((r) => (r.body as { fingerprint: string }).fingerprint)).toEqual([
      world.retention['30d'].fingerprint,
      preview(43, 900_000_000).fingerprint,
    ])
  })
})

describe('deleting recordings now', () => {
  it('lists the choices with exact counts and deletes the chosen ones', async () => {
    const world = backend()
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Delete recordings now…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete recordings' })
    expect(await within(dialog).findByText('(41 recordings, 0.8 GB)')).toBeInTheDocument()
    expect(within(dialog).getByText('Older than 30 days')).toBeInTheDocument()
    expect(within(dialog).getByText('(58, 1.1 GB)')).toBeInTheDocument()
    expect(within(dialog).getByText('One subject')).toBeInTheDocument()
    const subject = within(dialog).getByLabelText('Subject')
    expect(subject).not.toHaveAttribute('lang')
    expect(subject).toHaveClass('content')
    expect(
      within(dialog).getByText(/Transcripts, scores, feedback and trends stay\./),
    ).toBeInTheDocument()
    expect(
      within(dialog).getByText(
        'Recordings of answers that are still being assessed or failed processing are kept until they are resolved.',
      ),
    ).toBeInTheDocument()
    const button = within(dialog).getByRole('button', { name: 'Delete 41 recordings' })
    expect(button).toHaveClass('btn', 'danger', 'solid')

    await userEvent.click(within(dialog).getByRole('radio', { name: /All recordings/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete 58 recordings' }))
    expect(await screen.findByText('Deleted 41 recordings (0.8 GB).')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(changesOf(world)).toEqual([
      {
        method: 'POST',
        path: '/api/data/recordings/delete',
        body: { scope: 'all', fingerprint: world.all.fingerprint },
      },
    ])
  })

  it('deletes the recordings of one subject', async () => {
    const world = backend()
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Delete recordings now…' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('(41 recordings, 0.8 GB)')
    await userEvent.selectOptions(within(dialog).getByLabelText('Subject'), 'Biologia komórki')
    expect(within(dialog).getByRole('radio', { name: /One subject/ })).toBeChecked()
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Delete 18 recordings' }))
    await screen.findByText(/^Deleted 41 recordings/)
    expect(changesOf(world)[0].body).toEqual({
      scope: 'subject',
      subject_id: 2,
      fingerprint: world.subject.fingerprint,
    })
  })

  it('shows the numbers of the chosen subject even when an earlier answer comes last', async () => {
    const world = backend()
    server.use(
      http.get('/api/data/recordings/deletion-preview', async ({ request }) => {
        const url = new URL(request.url)
        if (url.searchParams.get('scope') !== 'subject') return undefined
        if (url.searchParams.get('subject_id') === '2') return HttpResponse.json(world.subject)
        await new Promise((resolve) => setTimeout(resolve, 80))
        return HttpResponse.json(preview(5, 1_000))
      }),
    )
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Delete recordings now…' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('(41 recordings, 0.8 GB)')
    await userEvent.click(within(dialog).getByRole('radio', { name: /One subject/ }))
    await userEvent.selectOptions(within(dialog).getByLabelText('Subject'), 'Biologia komórki')
    expect(await within(dialog).findByRole('button', { name: 'Delete 18 recordings' })).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 150))
    expect(within(dialog).getByRole('button', { name: 'Delete 18 recordings' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Delete 5 recordings' })).toBeNull()
  })

  it('keeps the dialog open with the new numbers after a 409', async () => {
    const world = backend({ changed: preview(40, 800_000_000) })
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Delete recordings now…' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Delete 41 recordings' }))
    expect(await within(dialog).findByText('The recordings changed since you looked.')).toBeInTheDocument()
    expect(within(dialog).getByText('(40 recordings, 763 MB)')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(changesOf(world)).toHaveLength(1)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete 40 recordings' }))
    await screen.findByText(/^Deleted 41 recordings/)
    expect(changesOf(world)).toHaveLength(2)
  })

  it('Escape cancels', async () => {
    backend()
    await renderSection()
    await userEvent.click(screen.getByRole('button', { name: 'Delete recordings now…' }))
    await screen.findByRole('dialog')
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('restoring', () => {
  async function chooseBackup() {
    await userEvent.click(screen.getByRole('button', { name: 'Restore from a backup…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Restore from a backup?' })
    await userEvent.upload(within(dialog).getByLabelText('Backup file'), zipFile())
    return dialog
  }

  it('shows what the backup holds and what it replaces, then restores', async () => {
    const world = backend()
    const { onRestored } = await renderSection()
    const dialog = await chooseBackup()
    expect(
      await within(dialog).findByText(
        'Backup from 15 Oct 2026: 2 subjects, 5 documents, 127 questions, 6 rounds, 49 recordings.',
      ),
    ).toBeInTheDocument()
    const warning = within(dialog).getByRole('status')
    expect(warning).toHaveClass('notice', 'warn')
    expect(warning).toHaveTextContent('Everything currently in the app is replaced by the backup')
    expect(warning).toHaveTextContent('including 1 round made after 15 Oct 2026.')
    expect(warning).toHaveTextContent('API keys are not in backups; the keys you have now stay.')
    expect(changesOf(world).map((r) => r.path)).toEqual(['/api/data/restore'])

    const apply = within(dialog).getByRole('button', { name: 'Replace and restore' })
    expect(apply).toHaveClass('btn', 'danger', 'solid')
    await userEvent.click(apply)
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1))
    expect(changesOf(world).map((r) => r.path)).toEqual([
      '/api/data/restore',
      `/api/data/restore/${world.restore.token}/apply`,
    ])
  })

  it('leaves out the newer rounds when there are none', async () => {
    backend({ restore: { ...restorePreview, newer_rounds: 0 } })
    await renderSection()
    const dialog = await chooseBackup()
    const warning = await within(dialog).findByRole('status')
    expect(warning).not.toHaveTextContent('made after')
  })

  it('cancelling discards the staged upload', async () => {
    const world = backend()
    const { onRestored } = await renderSection()
    const dialog = await chooseBackup()
    await within(dialog).findByText(/^Backup from/)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(changesOf(world).map((r) => [r.method, r.path])).toEqual([
      ['POST', '/api/data/restore'],
      ['DELETE', `/api/data/restore/${world.restore.token}`],
    ])
    expect(onRestored).not.toHaveBeenCalled()
  })

  it.each(['Cancel', 'Escape'])('%s while the file is checked discards the upload when it returns', async (how) => {
    const world = backend()
    let finish: () => void = () => undefined
    const held = new Promise<void>((resolve) => {
      finish = resolve
    })
    server.use(
      http.post('/api/data/restore', async ({ request }) => {
        world.requests.push({ method: request.method, path: new URL(request.url).pathname, body: undefined })
        await held
        return HttpResponse.json(world.restore)
      }),
    )
    const { onRestored } = await renderSection()
    const dialog = await chooseBackup()
    expect(await within(dialog).findByText('Checking the backup…')).toBeInTheDocument()
    if (how === 'Cancel') await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    else fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(changesOf(world).map((r) => r.method)).toEqual(['POST'])
    finish()
    await waitFor(() =>
      expect(changesOf(world).map((r) => [r.method, r.path])).toEqual([
        ['POST', '/api/data/restore'],
        ['DELETE', `/api/data/restore/${world.restore.token}`],
      ]),
    )
    expect(onRestored).not.toHaveBeenCalled()
  })

  it('shows the server message when the file is refused', async () => {
    backend({ uploadError: 'This file is not an AnswerOnCue backup.' })
    await renderSection()
    const dialog = await chooseBackup()
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'This file is not an AnswerOnCue backup.',
    )
    expect(within(dialog).getByLabelText('Backup file')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Replace and restore' })).toBeNull()
  })

  it('keeps the dialog when the restore is refused', async () => {
    backend({ applyError: 'A round or background work is running. Finish or pause it and try again.' })
    const { onRestored } = await renderSection()
    const dialog = await chooseBackup()
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Replace and restore' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('A round or background work is running.')
    expect(within(dialog).getByRole('button', { name: 'Replace and restore' })).toBeEnabled()
    expect(onRestored).not.toHaveBeenCalled()
  })

  it('lets another file be chosen', async () => {
    const world = backend()
    await renderSection()
    const dialog = await chooseBackup()
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Choose another file…' }))
    expect(await within(dialog).findByLabelText('Backup file')).toBeInTheDocument()
    expect(changesOf(world).map((r) => r.method)).toEqual(['POST', 'DELETE'])
  })
})
