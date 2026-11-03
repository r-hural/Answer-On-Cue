import { ApiError, apiSend } from '../../api/client'
import { browserLang, findBrowserVoice } from '../settings/browserVoice'
import type { QuestionAudio, RoundState } from './api'

/** The round's audio plus its content language, which the server checks the voice against. */
export type SpokenAudio = QuestionAudio & { language?: string | null }

export interface ReadingResult {
  /** How long the audio played, in ms; 0 when it never started. */
  playbackMs: number
  /** The voice could not be played: the question stays text only. */
  failed: boolean
  /** Why, when the server said (for example the spending ceiling); otherwise the usual note. */
  message?: string | null
}

export interface Reading {
  /** Resolves when the audio ended, was stopped, or failed. Never rejects. */
  done: Promise<ReadingResult>
  /** Resolves when the voice starts playing (or the reading ends without it). Never rejects. */
  started?: Promise<void>
  stop(): void
}

export interface Playback {
  /** Resolves when the audio ended or was stopped; rejects when it cannot play. */
  ended: Promise<void>
  stop(): void
}

export interface ReadDeps {
  now(): number
  /** The URL of the spoken question (premium voice). */
  speak(text: string, audio: SpokenAudio): Promise<string | null>
  playUrl(url: string): Playback
  /** The browser voice. */
  speakBrowser(text: string, rate: number | null | undefined, lang: string): Playback
}

/** Whether the question can be read aloud (so Replay makes sense). */
export function canReadAloud(audio: QuestionAudio): boolean {
  return audio.mode !== 'off'
}

function playUrl(url: string): Playback {
  const element = new Audio(url)
  let finish: () => void = () => {}
  let fail: (error: unknown) => void = () => {}
  const ended = new Promise<void>((resolve, reject) => {
    finish = resolve
    fail = reject
  })
  element.addEventListener('ended', () => finish())
  element.addEventListener('error', () => fail(new Error('The audio could not be played.')))
  void Promise.resolve(element.play()).catch(fail)
  return {
    ended,
    stop() {
      element.pause()
      finish()
    },
  }
}

function speakBrowser(text: string, rate: number | null | undefined, lang: string): Playback {
  const synth = window.speechSynthesis
  if (!synth) return { ended: Promise.reject(new Error('No browser voice.')), stop() {} }
  // Chrome loads the system voices late: an empty list speaks with the language tag alone.
  const voices = synth.getVoices()
  const voice = findBrowserVoice(voices, lang.split('-')[0])
  if (voices.length > 0 && !voice) {
    return { ended: Promise.reject(new Error('No browser voice for this language.')), stop() {} }
  }
  const utterance = new SpeechSynthesisUtterance(text)
  if (voice) utterance.voice = voice
  utterance.lang = lang
  if (rate) utterance.rate = rate
  let finish: () => void = () => {}
  let fail: (error: unknown) => void = () => {}
  const ended = new Promise<void>((resolve, reject) => {
    finish = resolve
    fail = reject
  })
  utterance.onend = () => finish()
  utterance.onerror = (event) => {
    // cancelling our own reading is not an error
    if (event.error === 'canceled' || event.error === 'interrupted') finish()
    else fail(new Error('The browser voice failed.'))
  }
  synth.cancel()
  synth.speak(utterance)
  return {
    ended,
    stop() {
      synth.cancel()
      finish()
    },
  }
}

/** Readings asked for once per page load: a prefetch and the reading itself share one request. */
const prepared = new Map<string, Promise<string | null>>()
/** Plenty for one round ahead; the oldest is dropped first. */
const PREPARED_MAX = 24

function preparedKey(text: string, audio: SpokenAudio): string {
  return JSON.stringify([text.trim(), audio.provider ?? null, audio.voice ?? null, audio.rate ?? null, audio.language ?? null])
}

async function requestSpeech(text: string, audio: SpokenAudio): Promise<string | null> {
  const body = {
    text,
    provider: audio.provider,
    voice: audio.voice,
    rate: audio.rate,
    // rounds created before the language was recorded send none
    ...(audio.language ? { language: audio.language } : {}),
  }
  const result = await apiSend<{ audio_url: string | null }>('POST', '/voice/speak', body)
  return result.audio_url
}

/** The spoken question's URL, sharing a request already on its way; a failed one is asked again next time. */
export function preparedSpeech(text: string, audio: SpokenAudio, request = requestSpeech): Promise<string | null> {
  const key = preparedKey(text, audio)
  const known = prepared.get(key)
  if (known) return known
  const pending = request(text, audio)
  prepared.set(key, pending)
  if (prepared.size > PREPARED_MAX) prepared.delete(prepared.keys().next().value as string)
  pending.then(
    (url) => {
      if (url === null) prepared.delete(key)
    },
    () => prepared.delete(key),
  )
  return pending
}

/** Starts making the premium voice of these questions now, so they play at once when asked. */
export function prefetchSpeech(texts: readonly string[], audio: SpokenAudio, request = requestSpeech): void {
  if (audio.mode !== 'premium') return
  for (const text of texts) {
    if (text.trim()) preparedSpeech(text, audio, request).catch(() => undefined) // the reading itself reports it
  }
}

/** The round's current question (while it is still to be read) and the ones that may come next. */
export function prefetchRound(state: RoundState, request = requestSpeech): void {
  if (state.status !== 'active' && state.status !== 'paused') return
  const texts = [...(state.phase === 'question' && state.current ? [state.current.text] : []), ...(state.upcoming ?? [])]
  prefetchSpeech(texts, { ...state.config.question_audio, language: state.config.language ?? null }, request)
}

/** Tests start from an empty store. */
export function clearPreparedSpeech(): void {
  prepared.clear()
}

const defaultDeps: ReadDeps = {
  now: () => performance.now(),
  speak: (text, audio) => preparedSpeech(text, audio),
  playUrl,
  speakBrowser,
}

/** Reads the question aloud with the round's voice. With audio off it is already done. */
export function startReading(text: string, audio: SpokenAudio, overrides: Partial<ReadDeps> = {}): Reading {
  const deps: ReadDeps = { ...defaultDeps, ...overrides }
  let stopped = false
  let playback: Playback | null = null
  let playStart: number | null = null
  const elapsed = () => (playStart === null ? 0 : Math.max(0, Math.round(deps.now() - playStart)))
  let markStopped: () => void = () => {}
  const stopping = new Promise<null>((resolve) => {
    markStopped = () => resolve(null)
  })
  let markStarted: () => void = () => {}
  const started = new Promise<void>((resolve) => {
    markStarted = resolve
  })

  const done = (async (): Promise<ReadingResult> => {
    if (audio.mode === 'off') return { playbackMs: 0, failed: false }
    try {
      if (audio.mode === 'premium') {
        // stopping while the voice is still being made ends the reading at once
        const url = await Promise.race([deps.speak(text, audio), stopping])
        if (stopped) return { playbackMs: 0, failed: false }
        if (!url) return { playbackMs: 0, failed: true }
        playStart = deps.now()
        playback = deps.playUrl(url)
      } else {
        playStart = deps.now()
        playback = deps.speakBrowser(text, audio.rate, browserLang(audio.language))
      }
      markStarted()
      await playback.ended
      return { playbackMs: elapsed(), failed: false }
    } catch (error) {
      const message = error instanceof ApiError && error.status === 409 ? error.message : null
      return { playbackMs: elapsed(), failed: true, message }
    }
  })()
  void done.then(() => markStarted())

  return {
    done,
    started,
    stop() {
      stopped = true
      markStopped()
      playback?.stop()
    },
  }
}
