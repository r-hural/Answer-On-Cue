import { act, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import type { ActionBody } from './api'
import { fakeRead, fakeSession, renderExam } from './examTestKit'
import { startReading } from './questionAudio'
import { config, setupRoundWorld, type RoundWorld } from './rounds.mocks'

const BLOCKED =
  'The monthly spending ceiling is reached: $15.00 of $15.00 used this month. Raise it in Settings → Spending.'
const premium = config({
  prep_seconds: 10,
  question_audio: { mode: 'premium', provider: 'azure_speech', voice: 'pl-PL-ZofiaNeural', rate: 1 },
})

function named(world: RoundWorld, action: string) {
  return world.requests.filter((r) => (r.body as ActionBody | undefined)?.action === action)
}

describe('the question voice refused by the spending ceiling', () => {
  it('passes the server message of a 409 through the reading result', async () => {
    const speak = vi.fn(async () => {
      throw new ApiError(409, BLOCKED, BLOCKED)
    })
    const reading = startReading('Pytanie?', premium.question_audio, { speak })
    await expect(reading.done).resolves.toEqual({ playbackMs: 0, failed: true, message: BLOCKED })
  })

  it('any other failure keeps the usual note', async () => {
    const speak = vi.fn(async () => {
      throw new ApiError(503, 'The provider is unavailable.')
    })
    const reading = startReading('Pytanie?', premium.question_audio, { speak })
    await expect(reading.done).resolves.toEqual({ playbackMs: 0, failed: true, message: null })
  })

  it('shows the reason in the voice note; the question stays text and preparation starts', async () => {
    const world = setupRoundWorld({ state: { config: premium } })
    const mic = fakeSession()
    const open = vi.fn(async () => mic.session)
    const voice = fakeRead()
    renderExam({ open, read: voice.read })
    await screen.findByText('Reading aloud')
    await act(async () => voice.readings[0].finish({ playbackMs: 0, failed: true, message: BLOCKED }))
    expect(await screen.findByText(BLOCKED)).toBeInTheDocument()
    expect(screen.queryByText('The question could not be read aloud.')).toBeNull()
    await waitFor(() => expect(named(world, 'question_ready')).toHaveLength(1))
    expect(await screen.findByText('Listening')).toBeInTheDocument()
  })
})
