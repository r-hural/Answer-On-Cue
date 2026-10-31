import { ApiError, apiSend } from '../../api/client'
import { browserLang, findBrowserVoice } from '../settings/browserVoice'
import type { QuestionAudio } from './api'

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

const defaultDeps: ReadDeps = {
  now: () => performance.now(),
  async speak(text, audio) {
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
  },
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

  const done = (async (): Promise<ReadingResult> => {
    if (audio.mode === 'off') return { playbackMs: 0, failed: false }
    try {
      if (audio.mode === 'premium') {
        const url = await deps.speak(text, audio)
        if (stopped) return { playbackMs: 0, failed: false }
        if (!url) return { playbackMs: 0, failed: true }
        playStart = deps.now()
        playback = deps.playUrl(url)
      } else {
        playStart = deps.now()
        playback = deps.speakBrowser(text, audio.rate, browserLang(audio.language))
      }
      await playback.ended
      return { playbackMs: elapsed(), failed: false }
    } catch (error) {
      const message = error instanceof ApiError && error.status === 409 ? error.message : null
      return { playbackMs: elapsed(), failed: true, message }
    }
  })()

  return {
    done,
    stop() {
      stopped = true
      playback?.stop()
    },
  }
}
