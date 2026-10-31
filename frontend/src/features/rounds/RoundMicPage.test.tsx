import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ListeningSession } from '../recording/capture'
import { RoundMicPage } from './RoundMicPage'
import { setupRoundWorld } from './rounds.mocks'

function fakeSession(available = true) {
  return {
    available,
    reason: available ? null : 'Microphone access is blocked. Allow the microphone in your browser and reload the page.',
    resampled: false,
    state: 'idle',
    noiseFloorDb: -60,
    startListening: vi.fn(),
    startRecording: vi.fn(),
    setMuted: vi.fn(),
    onLevel: vi.fn(() => () => {}),
    calibrate: vi.fn(async () => -52),
    stop: vi.fn(),
    dispose: vi.fn(),
  } as unknown as ListeningSession
}

function renderPage(open: () => Promise<ListeningSession>) {
  return render(
    <MemoryRouter initialEntries={['/practice/5/microphone']}>
      <Routes>
        <Route path="/practice/:roundId/microphone" element={<RoundMicPage open={open} />} />
        <Route path="/practice" element={<p>Setup page</p>} />
        <Route path="/rounds/:roundId" element={<p>Exam page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
})

describe('RoundMicPage', () => {
  it('shows the round crumbs and keeps "Start the round" disabled until access works', async () => {
    setupRoundWorld()
    renderPage(async () => fakeSession())
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toHaveTextContent('New round › Microphone check')
    expect(screen.getByRole('link', { name: 'New round' })).toHaveAttribute('href', '/practice')
    expect(screen.getByRole('button', { name: 'Start the round' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByText('The microphone works.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start the round' })).toBeEnabled()
  })

  it('stays disabled when access is denied', async () => {
    setupRoundWorld()
    renderPage(async () => fakeSession(false))
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Microphone access is blocked.')
    expect(screen.getByRole('button', { name: 'Start the round' })).toBeDisabled()
  })

  it('stores the calibrated floor and opens the exam', async () => {
    setupRoundWorld()
    renderPage(async () => fakeSession())
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Calibrate silence' }))
    expect(await screen.findByText('Noise level: −52 dB')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Start the round' }))
    expect(await screen.findByText('Exam page')).toBeInTheDocument()
    expect(sessionStorage.getItem('aoc.noiseFloorDb')).toBe('-52')
  })

  it('"Back to setup" abandons the round and returns to the setup', async () => {
    const world = setupRoundWorld()
    renderPage(async () => fakeSession())
    await userEvent.click(screen.getByRole('button', { name: 'Back to setup' }))
    expect(await screen.findByText('Setup page')).toBeInTheDocument()
    const abandon = world.requests.find((r) => r.path === '/api/rounds/5/actions')
    expect(abandon?.body).toEqual({ action: 'abandon', from_phase: 'question', attempt_id: 10 })
  })
})

describe('RoundMicPage: resume mode (?resume=1)', () => {
  function Location() {
    const location = useLocation()
    return <p data-testid="location">{location.pathname + location.search}</p>
  }

  function renderResume(open: () => Promise<ListeningSession>) {
    return render(
      <MemoryRouter initialEntries={['/practice/5/microphone?resume=1']}>
        <Location />
        <Routes>
          <Route path="/practice/:roundId/microphone" element={<RoundMicPage open={open} />} />
          <Route path="/" element={<p>Home page</p>} />
          <Route path="/rounds/:roundId" element={<p>Exam page</p>} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('"Not now" goes Home and leaves the round as it is', async () => {
    const world = setupRoundWorld()
    renderResume(async () => fakeSession())
    expect(screen.queryByRole('button', { name: 'Back to setup' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(world.requests.filter((r) => r.path === '/api/rounds/5/actions')).toEqual([])
  })

  it('"Start the round" opens the exam in resume mode', async () => {
    setupRoundWorld()
    renderResume(async () => fakeSession())
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByText('The microphone works.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Start the round' }))
    expect(await screen.findByText('Exam page')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/rounds\/5\?resume=1$/)
  })
})
