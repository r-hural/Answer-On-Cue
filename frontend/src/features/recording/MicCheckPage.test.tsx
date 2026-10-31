import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../test/msw'
import type { CaptureEvents, ListeningSession, SessionState } from './capture'
import { MicCheckPage } from './MicCheckPage'

const events: CaptureEvents = {
  listeningStartAt: 0,
  speechDetectedAt: null,
  captureStartAt: 0,
  stoppedAt: 0,
  preRollMs: 0,
  autoStarted: false,
}

function fakeSession() {
  let onSpeech: ((onsetMs: number) => void) | null = null
  let onLevel: ((db: number, tMs: number) => void) | null = null
  let finishCalibration: ((db: number) => void) | null = null
  let state: SessionState = 'idle'
  const session = {
    available: true,
    reason: null,
    resampled: false,
    get state() {
      return state
    },
    noiseFloorDb: -60,
    startListening: vi.fn((cb: (onsetMs: number) => void) => {
      onSpeech = cb
      state = 'listening'
    }),
    startRecording: vi.fn(() => {
      state = 'recording'
    }),
    setMuted: vi.fn(),
    onLevel: vi.fn((cb: (db: number, tMs: number) => void) => {
      onLevel = cb
      return () => {}
    }),
    calibrate: vi.fn(
      () =>
        new Promise<number>((resolve) => {
          finishCalibration = resolve
        }),
    ),
    stop: vi.fn(async () => {
      state = 'stopped'
      return { wav: new Blob([new Uint8Array(1044)], { type: 'audio/wav' }), durationMs: 2000, events }
    }),
    dispose: vi.fn(),
  } satisfies ListeningSession
  return {
    session,
    open: vi.fn(async () => session as ListeningSession),
    speak: (onsetMs: number) => act(() => onSpeech?.(onsetMs)),
    level: (db: number) => onLevel?.(db, 0),
    calibrated: (db: number) => act(() => finishCalibration?.(db)),
  }
}

function backend(intervals: Array<[number, number]>) {
  server.use(
    http.post('/api/recordings', () =>
      HttpResponse.json({ id: 5, duration_ms: 2000, purpose: 'mic_check' }, { status: 201 }),
    ),
    http.post('/api/recordings/5/analyze', () =>
      HttpResponse.json({
        engine: 'webrtcvad-2/30ms',
        speech_intervals: intervals,
        speech_ms: intervals.reduce((n, [a, b]) => n + b - a, 0),
        leading_silence_ms: intervals[0]?.[0] ?? 2000,
        trailing_silence_ms: 0,
        duration_ms: 2000,
      }),
    ),
  )
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:recording')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('MicCheckPage', () => {
  it('explains a denied microphone in English', async () => {
    const reason = 'Microphone access is blocked. Allow the microphone in your browser and reload the page.'
    const open = vi.fn(async () => ({ ...fakeSession().session, available: false, reason }) as ListeningSession)
    render(<MicCheckPage open={open} />)
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(reason)
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Listening test' })).not.toBeInTheDocument()
  })

  it('shows a short loud sound that is over before the meter refreshes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const mic = fakeSession()
    render(<MicCheckPage open={mic.open} />)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(screen.getByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByText('The microphone works.')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(100))
    // A 60 ms click between two refreshes (every 100 ms), then quiet frames again.
    mic.level(-70)
    mic.level(-6)
    mic.level(-71)
    mic.level(-72)
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '-6')
    // The next refresh shows only what came after it.
    mic.level(-65)
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '-65')
  })

  it('shows the live level and the calibrated noise floor', async () => {
    const mic = fakeSession()
    render(<MicCheckPage open={mic.open} />)
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByText('The microphone works.')).toBeInTheDocument()
    mic.level(-42.4)
    await vi.waitFor(() => expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '-42'))
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuetext', '−42 dB')

    await userEvent.click(screen.getByRole('button', { name: 'Calibrate silence' }))
    expect(mic.session.calibrate).toHaveBeenCalledWith(3000)
    expect(screen.getByText('Stay quiet for 3 seconds…')).toBeInTheDocument()
    await mic.calibrated(-52.4)
    expect(await screen.findByText('Noise level: −52 dB')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Listening test' })).toBeEnabled()
  })

  it('auto-starts on speech, stops by hand and shows the VAD result', async () => {
    backend([[1000, 2500]])
    const mic = fakeSession()
    render(<MicCheckPage open={mic.open} />)
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Listening test' }))
    expect(screen.getByRole('timer', { name: 'Preparation' })).toHaveTextContent('Preparation: 10 s')
    expect(mic.session.startListening).toHaveBeenCalled()

    await mic.speak(1234)
    expect(screen.getByText('Speech detected — recording')).toBeInTheDocument()
    expect(screen.getByText('Recording')).toBeInTheDocument()
    const stop = screen.getByRole('button', { name: 'Stop' })
    expect(stop).toHaveFocus()

    await userEvent.keyboard('{Enter}')
    expect(await screen.findByText('Speech from 1.0 s to 2.5 s')).toBeInTheDocument()
    expect(mic.session.stop).toHaveBeenCalledTimes(1)
    expect(mic.session.dispose).toHaveBeenCalled()
    expect(mic.session.startRecording).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Play back the recording')).toHaveAttribute('src', 'blob:recording')
    expect(screen.getByRole('button', { name: 'Repeat the test' })).toBeInTheDocument()
  })

  it('starts by timer after the countdown and stops after 8 s', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    backend([])
    const mic = fakeSession()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(<MicCheckPage open={mic.open} />)
    await user.click(screen.getByRole('button', { name: 'Allow microphone' }))
    await user.click(await screen.findByRole('button', { name: 'Listening test' }))

    await act(() => vi.advanceTimersByTimeAsync(9000))
    expect(screen.getByRole('timer')).toHaveTextContent('Preparation: 1 s')
    expect(mic.session.startRecording).not.toHaveBeenCalled()
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(mic.session.startRecording).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Preparation over — recording')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(8000))
    expect(mic.session.stop).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
    expect(await screen.findByText('No speech detected in the recording.')).toBeInTheDocument()
  })

  it('shows the backend error when the upload fails', async () => {
    server.use(
      http.post('/api/recordings', () =>
        HttpResponse.json({ detail: 'The recording must be in WAV format, 16 kHz, mono.' }, { status: 415 }),
      ),
    )
    const mic = fakeSession()
    render(<MicCheckPage open={mic.open} />)
    await userEvent.click(screen.getByRole('button', { name: 'Allow microphone' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Listening test' }))
    await mic.speak(0)
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The recording must be in WAV format, 16 kHz, mono.')
  })
})
