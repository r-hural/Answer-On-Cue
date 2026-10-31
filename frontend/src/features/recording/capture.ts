/**
 * Microphone listening and raw-PCM capture at 16 kHz mono.
 *
 * Before recording starts, audio only feeds a ring buffer and the speech trigger; nothing is kept.
 * When speech is confirmed (or recording is started by hand) the pre-roll from the ring buffer is
 * prepended, so the first words are not lost.
 */
import { DEFAULT_TRIGGER, SILENCE_DB, SpeechTrigger, calibrateNoiseFloor, rmsDb, type TriggerConfig } from './levels'
import { PcmRingBuffer, encodeWav } from './pcm'
import workletUrl from './pcm-worklet.js?url'
import { recordingStrings } from './strings'

export const SAMPLE_RATE = 16000
export const PRE_ROLL_MS = 1000
/** Used until the noise floor is calibrated or given. */
export const DEFAULT_NOISE_FLOOR_DB = -60
/** A calibrated floor is never taken below this, so digital silence does not make every hiss speech. */
export const MIN_NOISE_FLOOR_DB = -80
/** Ring capacity: the pre-roll plus the time the trigger needs to confirm speech, with room to spare. */
const RING_SECONDS = 2

export interface CaptureEvents {
  listeningStartAt: number | null
  speechDetectedAt: number | null
  captureStartAt: number | null
  stoppedAt: number | null
  preRollMs: number
  autoStarted: boolean
}

export type SessionState = 'idle' | 'listening' | 'recording' | 'stopped'

export interface ListeningSession {
  readonly available: boolean
  /** English explanation when `available` is false. */
  readonly reason: string | null
  /** True when the browser did not honour 16 kHz and the worklet downsampled. */
  readonly resampled: boolean
  readonly state: SessionState
  readonly noiseFloorDb: number
  startListening(onSpeechStart: (onsetMs: number) => void): void
  startRecording(): void
  setMuted(muted: boolean): void
  /** Level of every 20 ms frame, for a meter. Returns an unsubscribe function. */
  onLevel(listener: (levelDb: number, tMs: number) => void): () => void
  /** Measures the noise floor over `durationMs` of (unmuted) audio and uses it from then on. */
  calibrate(durationMs: number): Promise<number>
  stop(): Promise<{ wav: Blob; durationMs: number; events: CaptureEvents }>
  dispose(): void
}

/** The audio graph behind a session: a port that delivers 16 kHz Float32 chunks. */
export interface CapturePipeline {
  port: { onmessage: ((event: { data: Float32Array }) => void) | null }
  resampled: boolean
  close(): void
}

export interface CaptureDeps {
  getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream>
  createPipeline(stream: MediaStream): Promise<CapturePipeline>
  now(): number
}

export interface ListeningOptions {
  noiseFloorDb?: number
  trigger?: TriggerConfig
  deps?: Partial<CaptureDeps>
}

export const AUDIO_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: false },
}

function emptyEvents(): CaptureEvents {
  return {
    listeningStartAt: null,
    speechDetectedAt: null,
    captureStartAt: null,
    stoppedAt: null,
    preRollMs: 0,
    autoStarted: false,
  }
}

function errorReason(error: unknown): string {
  const name = error instanceof Error || error instanceof DOMException ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError') {
    return recordingStrings.errors.denied
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError' || name === 'DevicesNotFoundError') {
    return recordingStrings.errors.notFound
  }
  return recordingStrings.errors.failed
}

async function webAudioPipeline(stream: MediaStream): Promise<CapturePipeline> {
  const build = async (ctx: AudioContext): Promise<CapturePipeline> => {
    const source = ctx.createMediaStreamSource(stream)
    await ctx.audioWorklet.addModule(workletUrl)
    const node = new AudioWorkletNode(ctx, 'pcm-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      processorOptions: { targetRate: SAMPLE_RATE },
    })
    // A silent sink keeps the worklet pulled by the graph without playing anything back.
    const sink = ctx.createGain()
    sink.gain.value = 0
    source.connect(node)
    node.connect(sink)
    sink.connect(ctx.destination)
    await ctx.resume()
    return {
      port: node.port as unknown as CapturePipeline['port'],
      resampled: ctx.sampleRate !== SAMPLE_RATE,
      close: () => {
        source.disconnect()
        node.disconnect()
        node.port.onmessage = null
        void ctx.close()
      },
    }
  }
  let ctx: AudioContext | null = null
  try {
    ctx = new AudioContext({ sampleRate: SAMPLE_RATE })
    return await build(ctx)
  } catch (error) {
    // Some browsers refuse a microphone in a context at another rate; fall back to the native rate.
    if (ctx) void ctx.close()
    if (error instanceof DOMException && error.name === 'NotSupportedError') return build(new AudioContext())
    throw error
  }
}

const defaultDeps: CaptureDeps = {
  getUserMedia: (constraints) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      return Promise.reject(new DOMException('No media devices', 'NotFoundError'))
    }
    return navigator.mediaDevices.getUserMedia(constraints)
  },
  createPipeline: webAudioPipeline,
  now: () => performance.now(),
}

function concat(parts: Float32Array[]): Float32Array {
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0))
  let offset = 0
  for (const part of parts) {
    out.set(part, offset)
    offset += part.length
  }
  return out
}

const msOf = (samples: number) => (samples * 1000) / SAMPLE_RATE

class UnavailableSession implements ListeningSession {
  readonly available = false
  readonly resampled = false
  readonly state: SessionState = 'idle'
  readonly noiseFloorDb = SILENCE_DB
  readonly reason: string

  constructor(reason: string) {
    this.reason = reason
  }

  startListening(): void {}
  startRecording(): void {}
  setMuted(): void {}
  onLevel(): () => void {
    return () => {}
  }
  calibrate(): Promise<number> {
    return Promise.resolve(SILENCE_DB)
  }
  stop() {
    return Promise.resolve({ wav: encodeWav(new Float32Array(0), SAMPLE_RATE), durationMs: 0, events: emptyEvents() })
  }
  dispose(): void {}
}

interface Calibration {
  remaining: number
  levels: number[]
  resolve: (floorDb: number) => void
}

class LiveSession implements ListeningSession {
  readonly available = true
  readonly reason = null
  readonly resampled: boolean
  state: SessionState = 'idle'
  noiseFloorDb: number

  private readonly ring = new PcmRingBuffer(SAMPLE_RATE, RING_SECONDS)
  private readonly levelListeners = new Set<(levelDb: number, tMs: number) => void>()
  private readonly recorded: Float32Array[] = []
  private readonly events = emptyEvents()
  private trigger: SpeechTrigger
  private onSpeechStart: ((onsetMs: number) => void) | null = null
  private calibration: Calibration | null = null
  private origin: number | null = null
  private listenSample = 0
  private muted = false
  private disposed = false
  private readonly cfg: TriggerConfig
  private readonly deps: CaptureDeps
  private readonly stream: MediaStream
  private readonly pipeline: CapturePipeline

  constructor(stream: MediaStream, pipeline: CapturePipeline, deps: CaptureDeps, floorDb: number, cfg: TriggerConfig) {
    this.stream = stream
    this.pipeline = pipeline
    this.deps = deps
    this.cfg = cfg
    this.resampled = pipeline.resampled
    this.noiseFloorDb = floorDb
    this.trigger = new SpeechTrigger(floorDb, cfg)
    pipeline.port.onmessage = (event) => this.handleChunk(event.data)
  }

  /** performance.now()-based time of an absolute sample index. */
  private timeOf(sample: number): number {
    return (this.origin ?? this.deps.now()) + msOf(sample)
  }

  private handleChunk(chunk: Float32Array): void {
    if (this.disposed || this.state === 'stopped') return
    const startSample = this.ring.totalSamples
    if (this.origin === null) this.origin = this.deps.now() - msOf(chunk.length)
    this.ring.push(chunk)
    const tMs = this.timeOf(startSample)
    const level = rmsDb(chunk)
    for (const listener of this.levelListeners) listener(level, tMs)

    if (this.calibration && !this.muted) {
      const cal = this.calibration
      cal.levels.push(level)
      cal.remaining -= msOf(chunk.length)
      if (cal.remaining <= 0) {
        this.calibration = null
        this.setNoiseFloor(Math.max(MIN_NOISE_FLOOR_DB, calibrateNoiseFloor(cal.levels)))
        cal.resolve(this.noiseFloorDb)
      }
    }

    if (this.state === 'recording') {
      this.recorded.push(chunk)
    } else if (this.state === 'listening') {
      const onset = this.trigger.push(level, tMs)
      if (onset !== null) {
        this.events.speechDetectedAt = this.deps.now()
        this.events.autoStarted = true
        const onsetSample = startSample - Math.round(((tMs - onset) * SAMPLE_RATE) / 1000)
        this.beginRecording(onsetSample, onset)
        this.onSpeechStart?.(onset)
      }
    }
  }

  private setNoiseFloor(floorDb: number): void {
    this.noiseFloorDb = floorDb
    this.trigger = new SpeechTrigger(floorDb, this.cfg)
    this.trigger.setMuted(this.muted)
  }

  /** Starts keeping audio from `fromSample` minus the pre-roll (never before listening began). */
  private beginRecording(fromSample: number, captureStartAt: number): void {
    const total = this.ring.totalSamples
    const snapshot = this.ring.snapshot()
    const oldest = total - snapshot.length
    const preRollStart = Math.max(fromSample - (PRE_ROLL_MS * SAMPLE_RATE) / 1000, this.listenSample, oldest)
    this.recorded.push(snapshot.slice(preRollStart - oldest))
    this.events.captureStartAt = captureStartAt
    this.events.preRollMs = Math.max(0, msOf(fromSample - preRollStart))
    this.state = 'recording'
  }

  startListening(onSpeechStart: (onsetMs: number) => void): void {
    if (this.state !== 'idle') return
    this.onSpeechStart = onSpeechStart
    this.listenSample = this.ring.totalSamples
    this.events.listeningStartAt = this.deps.now()
    this.trigger = new SpeechTrigger(this.noiseFloorDb, this.cfg)
    this.trigger.setMuted(this.muted)
    this.state = 'listening'
  }

  startRecording(): void {
    if (this.state === 'listening') {
      this.beginRecording(this.ring.totalSamples, this.deps.now())
    } else if (this.state === 'idle') {
      this.events.captureStartAt = this.deps.now()
      this.events.preRollMs = 0
      this.state = 'recording'
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted
    this.trigger.setMuted(muted)
  }

  onLevel(listener: (levelDb: number, tMs: number) => void): () => void {
    this.levelListeners.add(listener)
    return () => this.levelListeners.delete(listener)
  }

  calibrate(durationMs: number): Promise<number> {
    return new Promise((resolve) => {
      this.calibration?.resolve(this.noiseFloorDb)
      this.calibration = { remaining: durationMs, levels: [], resolve }
    })
  }

  stop() {
    if (this.state !== 'stopped') {
      this.events.stoppedAt = this.deps.now()
      this.state = 'stopped'
    }
    const samples = concat(this.recorded)
    return Promise.resolve({
      wav: encodeWav(samples, SAMPLE_RATE),
      durationMs: msOf(samples.length),
      events: { ...this.events },
    })
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.calibration?.resolve(this.noiseFloorDb)
    this.calibration = null
    this.levelListeners.clear()
    this.pipeline.port.onmessage = null
    this.pipeline.close()
    for (const track of this.stream.getTracks()) track.stop()
  }
}

export async function openListeningSession(opts: ListeningOptions = {}): Promise<ListeningSession> {
  const deps: CaptureDeps = { ...defaultDeps, ...opts.deps }
  let stream: MediaStream
  try {
    stream = await deps.getUserMedia(AUDIO_CONSTRAINTS)
  } catch (error) {
    return new UnavailableSession(errorReason(error))
  }
  try {
    const pipeline = await deps.createPipeline(stream)
    return new LiveSession(stream, pipeline, deps, opts.noiseFloorDb ?? DEFAULT_NOISE_FLOOR_DB, opts.trigger ?? DEFAULT_TRIGGER)
  } catch (error) {
    for (const track of stream.getTracks()) track.stop()
    return new UnavailableSession(errorReason(error))
  }
}
