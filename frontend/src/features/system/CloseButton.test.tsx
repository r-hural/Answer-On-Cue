import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import { CloseButton } from './routes'

afterEach(() => {
  vi.restoreAllMocks()
})

function infoHandler(runningJobs: number, counter?: { gets: number }) {
  return http.get('/api/system/info', () => {
    if (counter) counter.gets += 1
    return HttpResponse.json({
      version: '0.1.0',
      installed: true,
      latest: null,
      update_available: false,
      checked_at: null,
      check_message: null,
      running_jobs: runningJobs,
      last_update: null,
    })
  })
}

describe('CloseButton', () => {
  it('fetches nothing until it is clicked', () => {
    const counter = { gets: 0 }
    server.use(infoHandler(0, counter))
    render(<CloseButton />)
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
    expect(counter.gets).toBe(0)
  })

  it('confirms, posts the shutdown with the guard header and shows the closed page', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    let guard: string | null = null
    server.use(
      infoHandler(0),
      http.post('/api/system/shutdown', ({ request }) => {
        guard = request.headers.get('X-AnswerOnCue')
        return HttpResponse.json({ message: 'The program is shutting down.' }, { status: 202 })
      }),
    )
    render(<CloseButton />)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(
      await screen.findByText(
        'The program has been closed. You can close this tab. To start it again, use the “AnswerOnCue” shortcut on your desktop.',
      ),
    ).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledWith('Close the AnswerOnCue program?')
    expect(guard).toBe('1')
  })

  it('warns about running tasks', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    server.use(infoHandler(2))
    render(<CloseButton />)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await vi.waitFor(() => expect(confirm).toHaveBeenCalled())
    const text = confirm.mock.calls[0][0] as string
    expect(text).toContain('Close the AnswerOnCue program?')
    expect(text).toContain('2 running task(s) will be interrupted; you can resume them in the Library.')
  })

  it('does not shut down when the confirmation is declined', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    let posted = 0
    server.use(
      infoHandler(0),
      http.post('/api/system/shutdown', () => {
        posted += 1
        return HttpResponse.json({}, { status: 202 })
      }),
    )
    render(<CloseButton />)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await vi.waitFor(() => expect(window.confirm).toHaveBeenCalled())
    expect(posted).toBe(0)
  })

  it('works with the keyboard', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    server.use(
      infoHandler(0),
      http.post('/api/system/shutdown', () => HttpResponse.json({}, { status: 202 })),
    )
    render(<CloseButton />)
    await user.tab()
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('status')).toHaveTextContent('The program has been closed.')
  })

  it('reports a failure', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    server.use(
      infoHandler(0),
      http.post('/api/system/shutdown', () => new HttpResponse(null, { status: 403 })),
    )
    render(<CloseButton />)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(await screen.findByText('Could not close the program.')).toBeInTheDocument()
  })
})
