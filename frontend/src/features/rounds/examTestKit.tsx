import { act, render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import type { CaptureEvents, ListeningSession, SessionState } from '../recording/capture'
import { ExamPage, type ExamPageProps } from './ExamPage'
import type { Reading, ReadingResult } from './questionAudio'
import { createUploadQueue } from './uploads'

export const CAPTURE_EVENTS: CaptureEvents = {
  listeningStartAt: 1000,
  speechDetectedAt: 4000,
  captureStartAt: 4000,
  stoppedAt: 9000,
  preRollMs: 800,
  autoStarted: true,
}

export function fakeSession(overrides: Record<string, unknown> = {}) {
  let onSpeech: ((onsetMs: number) => void) | null = null
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
    onLevel: vi.fn(() => () => {}),
    calibrate: vi.fn(async () => -60),
    stop: vi.fn(async () => {
      state = 'stopped'
      return { wav: new Blob([new Uint8Array(100)], { type: 'audio/wav' }), durationMs: 2000, events: CAPTURE_EVENTS }
    }),
    dispose: vi.fn(),
    ...overrides,
  }
  return {
    session: session as unknown as ListeningSession & typeof session,
    /** The trigger confirms speech that began at `onsetMs` (the capture goes on recording by itself). */
    speak: (onsetMs: number) =>
      act(() => {
        state = 'recording'
        onSpeech?.(onsetMs)
      }),
  }
}

/** A question voice that plays until the test finishes it (or "Read it myself" stops it). */
export function fakeRead() {
  const readings: { reading: Reading; finish: (result: ReadingResult) => void }[] = []
  const read = vi.fn(() => {
    let finish: (result: ReadingResult) => void = () => {}
    const done = new Promise<ReadingResult>((resolve) => {
      finish = resolve
    })
    const reading: Reading = { done, stop: vi.fn(() => finish({ playbackMs: 400, failed: false })) }
    readings.push({ reading, finish })
    return reading
  })
  return { read, readings }
}

export function renderExam(props: ExamPageProps & { path?: string } = {}) {
  const { path = '/rounds/5', ...page } = props
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/rounds/:roundId"
          element={<ExamPage uploads={createUploadQueue()} pollMs={50} {...page} />}
        />
        <Route path="/rounds/:roundId/summary" element={<p>Summary page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}
