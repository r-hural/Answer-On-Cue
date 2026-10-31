import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { SystemInfo } from './api'
import { POLL_INTERVAL_MS, ProgramPage, UPDATE_TIMEOUT_MS } from './ProgramPage'

function info(overrides: Partial<SystemInfo> = {}): SystemInfo {
  return {
    version: '0.1.0',
    installed: true,
    latest: '0.1.0',
    update_available: false,
    checked_at: '2026-10-21T10:00:00Z',
    check_message: null,
    running_jobs: 0,
    last_update: null,
    ...overrides,
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('ProgramPage', () => {
  it('shows the version and that it is up to date', async () => {
    server.use(http.get('/api/system/info', () => HttpResponse.json(info())))
    render(<ProgramPage />)
    expect(await screen.findByText('Version 0.1.0')).toBeInTheDocument()
    expect(screen.getByText('You have the latest version.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument()
  })

  it('shows the check message when there is no version to compare', async () => {
    server.use(
      http.get('/api/system/info', () =>
        HttpResponse.json(
          info({ latest: null, installed: false, check_message: 'No version has been published yet.' }),
        ),
      ),
    )
    render(<ProgramPage />)
    expect(await screen.findByText('No version has been published yet.')).toBeInTheDocument()
  })

  it('asks for confirmation, updates and reloads when the version changes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    let posted = 0
    let healthVersion = '0.1.0'
    server.use(
      http.get('/api/system/info', () =>
        HttpResponse.json(info({ latest: '0.2.0', update_available: true })),
      ),
      http.post('/api/system/update', ({ request }) => {
        posted += 1
        expect(request.headers.get('X-AnswerOnCue')).toBe('1')
        return HttpResponse.json({ version: '0.2.0' }, { status: 202 })
      }),
      http.get('/api/health', () =>
        HttpResponse.json({ status: 'ok', app: 'answeroncue', version: healthVersion }),
      ),
    )
    const reload = vi.fn()
    render(<ProgramPage reload={reload} />)
    expect(await screen.findByText('Version 0.2.0 is available.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Update' }))
    expect(confirm).toHaveBeenCalledWith(
      'The program will close briefly and restart. Continue?',
    )
    expect(await screen.findByText('Updating… This page will refresh by itself.')).toBeInTheDocument()
    expect(posted).toBe(1)
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2))
    expect(reload).not.toHaveBeenCalled()
    healthVersion = '0.2.0'
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('does nothing when the confirmation is declined', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    let posted = 0
    server.use(
      http.get('/api/system/info', () =>
        HttpResponse.json(info({ latest: '0.2.0', update_available: true })),
      ),
      http.post('/api/system/update', () => {
        posted += 1
        return HttpResponse.json({ version: '0.2.0' }, { status: 202 })
      }),
    )
    render(<ProgramPage />)
    await user.click(await screen.findByRole('button', { name: 'Update' }))
    expect(posted).toBe(0)
  })

  it('shows the server message when the update is refused', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    server.use(
      http.get('/api/system/info', () =>
        HttpResponse.json(info({ latest: '0.2.0', update_available: true })),
      ),
      http.post('/api/system/update', () =>
        HttpResponse.json({ detail: 'An update is already running.' }, { status: 409 }),
      ),
    )
    render(<ProgramPage />)
    await user.click(await screen.findByRole('button', { name: 'Update' }))
    expect(await screen.findByText('An update is already running.')).toBeInTheDocument()
  })

  it('gives up after ten minutes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    server.use(
      http.get('/api/system/info', () =>
        HttpResponse.json(info({ latest: '0.2.0', update_available: true })),
      ),
      http.post('/api/system/update', () => HttpResponse.json({ version: '0.2.0' }, { status: 202 })),
      http.get('/api/health', () => new HttpResponse(null, { status: 503 })),
    )
    const reload = vi.fn()
    render(<ProgramPage reload={reload} />)
    await user.click(await screen.findByRole('button', { name: 'Update' }))
    await screen.findByText('Updating… This page will refresh by itself.')
    await act(() => vi.advanceTimersByTimeAsync(UPDATE_TIMEOUT_MS + POLL_INTERVAL_MS))
    expect(
      screen.getByText('The update is taking too long. Start the program from the desktop shortcut.'),
    ).toBeInTheDocument()
    expect(reload).not.toHaveBeenCalled()
  })

  it('shows a failed last update', async () => {
    server.use(
      http.get('/api/system/info', () =>
        HttpResponse.json(
          info({
            last_update: {
              ok: false,
              version: '0.2.0',
              from_version: '0.1.0',
              message: 'The migration failed.',
              at: '2026-10-21T10:00:00Z',
            },
          }),
        ),
      ),
    )
    render(<ProgramPage />)
    expect(
      await screen.findByText('The last update failed: The migration failed.'),
    ).toBeInTheDocument()
  })

  it('"Check again" asks for a refresh', async () => {
    const user = userEvent.setup()
    const seen: (string | null)[] = []
    server.use(
      http.get('/api/system/info', ({ request }) => {
        seen.push(new URL(request.url).searchParams.get('refresh'))
        return HttpResponse.json(info())
      }),
    )
    render(<ProgramPage />)
    await user.click(await screen.findByRole('button', { name: 'Check again' }))
    await vi.waitFor(() => expect(seen).toEqual([null, 'true']))
  })

  it('both buttons are reachable and work with Enter', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let updates = 0
    const refreshes: (string | null)[] = []
    server.use(
      http.get('/api/system/info', ({ request }) => {
        refreshes.push(new URL(request.url).searchParams.get('refresh'))
        return HttpResponse.json(info({ latest: '0.2.0', update_available: true }))
      }),
      http.post('/api/system/update', () => {
        updates += 1
        return HttpResponse.json({ version: '0.2.0' }, { status: 202 })
      }),
    )
    render(<ProgramPage />)
    await screen.findByRole('button', { name: 'Update' })
    await user.tab()
    expect(screen.getByRole('button', { name: 'Update' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Check again' })).toHaveFocus()
    await user.keyboard('{Enter}')
    await vi.waitFor(() => expect(refreshes).toContain('true'))
    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Update' })).toHaveFocus()
    await user.keyboard('{Enter}')
    await vi.waitFor(() => expect(updates).toBe(1))
  })
})
