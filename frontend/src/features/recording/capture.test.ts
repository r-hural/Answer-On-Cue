import { describe, expect, it, vi } from 'vitest'
import { openListeningSession, type CaptureDeps, type CapturePipeline } from './capture'

const QUIET = 0.0001 // -80 dBFS
const SPEECH = 0.1 // -20 dBFS

function fakeMic(getUserMedia?: CaptureDeps['getUserMedia']) {
  let clock = 1000
  const track = { stop: vi.fn() }
  const stream = { getTracks: () => [track] } as unknown as MediaStream
  const close = vi.fn<() => void>()
  const pipeline: CapturePipeline = {
    port: { onmessage: null },
    resampled: false,
    close,
  }
  const deps: CaptureDeps = {
    getUserMedia: getUserMedia ?? vi.fn(async () => stream),
    createPipeline: vi.fn(async () => pipeline),
    now: () => clock,
  }
  /** Delivers `ms` of audio at a constant level in 20 ms chunks, advancing the clock. */
  const play = (amplitude: number, ms: number) => {
    for (let t = 0; t < ms; t += 20) {
      clock += 20
      pipeline.port.onmessage?.({ data: new Float32Array(320).fill(amplitude) })
    }
  }
  return { deps, play, track, pipeline, now: () => clock }
}

const wavMs = (wav: Blob) => ((wav.size - 44) / 2 / 16000) * 1000

describe('openListeningSession', () => {
  it('auto-starts on speech after 1.5 s of silence, keeping 1 s of pre-roll', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    expect(session.available).toBe(true)
    const onSpeech = vi.fn()
    session.startListening(onSpeech)
    const listenAt = mic.now()
    mic.play(QUIET, 1500)
    expect(session.state).toBe('listening')
    mic.play(SPEECH, 2000)
    expect(onSpeech).toHaveBeenCalledTimes(1)
    const onset = onSpeech.mock.calls[0][0]
    expect(onset).toBeCloseTo(listenAt + 1500, 0)
    expect(session.state).toBe('recording')

    const { wav, durationMs, events } = await session.stop()
    expect(durationMs).toBe(3500 - 1500 + 1000)
    expect(wavMs(wav)).toBe(durationMs)
    expect(events.autoStarted).toBe(true)
    expect(events.preRollMs).toBe(1000)
    expect(events.listeningStartAt).toBe(listenAt)
    expect(events.captureStartAt).toBeCloseTo(onset, 0)
    expect(events.speechDetectedAt).toBeGreaterThanOrEqual(onset + 300)
    expect(events.stoppedAt).toBe(mic.now())
  })

  it('keeps only the listened audio as pre-roll when speech comes early', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    mic.play(QUIET, 3000) // before listening: never part of the recording
    session.startListening(() => {})
    mic.play(QUIET, 400)
    mic.play(SPEECH, 1000)
    const { durationMs, events } = await session.stop()
    expect(events.preRollMs).toBe(400)
    expect(durationMs).toBe(1400)
  })

  it('manual start without listening has no pre-roll', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    mic.play(QUIET, 500)
    session.startRecording()
    mic.play(SPEECH, 1000)
    const { durationMs, events } = await session.stop()
    expect(durationMs).toBe(1000)
    expect(events.preRollMs).toBe(0)
    expect(events.autoStarted).toBe(false)
    expect(events.listeningStartAt).toBeNull()
    expect(events.speechDetectedAt).toBeNull()
  })

  it('manual start while listening keeps the pre-roll', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    session.startListening(() => {})
    mic.play(QUIET, 1600)
    session.startRecording()
    mic.play(QUIET, 500)
    const { durationMs, events } = await session.stop()
    expect(events.preRollMs).toBe(1000)
    expect(durationMs).toBe(1500)
    expect(events.autoStarted).toBe(false)
  })

  it('does not trigger while muted', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    const onSpeech = vi.fn()
    session.startListening(onSpeech)
    session.setMuted(true)
    mic.play(SPEECH, 2000)
    expect(onSpeech).not.toHaveBeenCalled()
    session.setMuted(false)
    mic.play(SPEECH, 400)
    expect(onSpeech).toHaveBeenCalledTimes(1)
  })

  it('calibrates the noise floor from unmuted frames only', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    const levels: number[] = []
    session.onLevel((db) => levels.push(db))
    const floor = session.calibrate(1000)
    session.setMuted(true)
    mic.play(SPEECH, 2000)
    session.setMuted(false)
    mic.play(0.01, 1000) // -40 dBFS
    expect(await floor).toBeCloseTo(-40, 1)
    expect(session.noiseFloorDb).toBeCloseTo(-40, 1)
    expect(levels).toHaveLength(150)

    // -40 + 12: a -30 dB voice is not enough any more, -20 dB is.
    const onSpeech = vi.fn()
    session.startListening(onSpeech)
    mic.play(0.0316, 1000)
    expect(onSpeech).not.toHaveBeenCalled()
    mic.play(SPEECH, 400)
    expect(onSpeech).toHaveBeenCalledTimes(1)
  })

  it('never calibrates below -80 dB', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    const floor = session.calibrate(500)
    mic.play(0, 600)
    expect(await floor).toBe(-80)
  })

  it('reports a denied permission in English', async () => {
    const mic = fakeMic(() => Promise.reject(new DOMException('denied', 'NotAllowedError')))
    const session = await openListeningSession({ deps: mic.deps })
    expect(session.available).toBe(false)
    expect(session.reason).toBe('Microphone access is blocked. Allow the microphone in your browser and reload the page.')
    expect(mic.deps.createPipeline).not.toHaveBeenCalled()
    const { durationMs } = await session.stop()
    expect(durationMs).toBe(0)
  })

  it('reports a missing microphone in English', async () => {
    const mic = fakeMic(() => Promise.reject(new DOMException('none', 'NotFoundError')))
    const session = await openListeningSession({ deps: mic.deps })
    expect(session.available).toBe(false)
    expect(session.reason).toBe('No microphone found.')
  })

  it('dispose stops the tracks and closes the pipeline', async () => {
    const mic = fakeMic()
    const session = await openListeningSession({ deps: mic.deps })
    session.dispose()
    expect(mic.track.stop).toHaveBeenCalled()
    expect(mic.pipeline.close).toHaveBeenCalled()
    expect(mic.pipeline.port.onmessage).toBeNull()
  })
})
