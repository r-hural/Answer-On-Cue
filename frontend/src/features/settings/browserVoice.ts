import { settingsStrings } from './strings'

/** Code → BCP 47, a copy of the backend profiles: the exam knows only the round's language code. */
export const BROWSER_LANG: Record<string, string> = {
  pl: 'pl-PL',
  en: 'en-GB',
  de: 'de-DE',
  fr: 'fr-FR',
  es: 'es-ES',
  it: 'it-IT',
  uk: 'uk-UA',
}

/** Rounds without a language (older ones) are Polish. */
export function browserLang(code: string | null | undefined): string {
  return (code && BROWSER_LANG[code]) || 'pl-PL'
}

/** The first system voice whose language starts with the code (`_` read as `-`, case ignored). */
export function findBrowserVoice(
  voices: SpeechSynthesisVoice[],
  code: string,
): SpeechSynthesisVoice | null {
  const prefix = code.toLowerCase()
  return (
    voices.find((voice) => voice.lang.toLowerCase().replace(/_/g, '-').startsWith(prefix)) ?? null
  )
}

/**
 * Speaks the language's sample sentence. An empty system list (Chrome loads voices late) speaks with
 * the language tag only; a list without a voice for the code speaks nothing.
 */
export function speakSample(code: string, bcp47: string, rate: number): boolean {
  const synth = typeof window === 'undefined' ? undefined : window.speechSynthesis
  if (!synth) return false
  const voices = synth.getVoices()
  const voice = findBrowserVoice(voices, code)
  if (voices.length > 0 && !voice) return false
  const utterance = new SpeechSynthesisUtterance(
    settingsStrings.languages.browserSample[code] ?? settingsStrings.languages.browserSample.pl,
  )
  if (voice) utterance.voice = voice
  utterance.lang = bcp47
  utterance.rate = rate
  synth.cancel()
  synth.speak(utterance)
  return true
}
