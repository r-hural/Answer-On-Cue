import { afterEach, describe, expect, it, vi } from 'vitest'
import { LANGUAGES } from '../library/languages.mocks'
import { BROWSER_LANG, browserLang, findBrowserVoice, speakSample } from './browserVoice'
import { settingsStrings } from './strings'

class FakeUtterance {
  text: string
  lang = ''
  rate = 1
  voice: unknown = null
  constructor(text: string) {
    this.text = text
  }
}

function stubSpeech(voices: { lang: string }[]) {
  const speak = vi.fn()
  const cancel = vi.fn()
  vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
  vi.stubGlobal('speechSynthesis', { getVoices: () => voices, speak, cancel })
  return { speak, cancel }
}

afterEach(() => vi.unstubAllGlobals())

describe('browserVoice', () => {
  it('BROWSER_LANG mirrors the backend profiles', () => {
    expect(BROWSER_LANG).toEqual(Object.fromEntries(LANGUAGES.map((row) => [row.code, row.bcp47])))
  })

  it('browserLang falls back to Polish', () => {
    expect(browserLang('de')).toBe('de-DE')
    expect(browserLang(null)).toBe('pl-PL')
    expect(browserLang('xx')).toBe('pl-PL')
  })

  it('findBrowserVoice reads _ as - and ignores case', () => {
    const voices = [
      { lang: 'pl-PL' },
      { lang: 'en_GB' },
      { lang: 'EN-us' },
    ] as SpeechSynthesisVoice[]
    expect(findBrowserVoice(voices, 'en')).toBe(voices[1])
    expect(findBrowserVoice(voices, 'de')).toBeNull()
  })

  it('speaks the sample with the language voice', () => {
    const voice = { lang: 'en-GB' }
    const { speak } = stubSpeech([voice])
    expect(speakSample('en', 'en-GB', 1.2)).toBe(true)
    const utterance = speak.mock.calls[0][0] as FakeUtterance
    expect(utterance.text).toBe(settingsStrings.languages.browserSample.en)
    expect(utterance.lang).toBe('en-GB')
    expect(utterance.rate).toBe(1.2)
    expect(utterance.voice).toBe(voice)
  })

  it('speaks nothing when the system has voices but none for the language', () => {
    const { speak } = stubSpeech([{ lang: 'pl-PL' }])
    expect(speakSample('en', 'en-GB', 1)).toBe(false)
    expect(speak).not.toHaveBeenCalled()
  })

  it('speaks with the language tag only when the system list is empty', () => {
    const { speak } = stubSpeech([])
    expect(speakSample('en', 'en-GB', 1)).toBe(true)
    const utterance = speak.mock.calls[0][0] as FakeUtterance
    expect(utterance.lang).toBe('en-GB')
    expect(utterance.voice).toBeNull()
  })

  it('returns false without speechSynthesis', () => {
    expect(speakSample('en', 'en-GB', 1)).toBe(false)
  })

  it('has a browser sample for every language', () => {
    expect(Object.keys(settingsStrings.languages.browserSample).sort()).toEqual(
      LANGUAGES.map((row) => row.code).sort(),
    )
  })
})
