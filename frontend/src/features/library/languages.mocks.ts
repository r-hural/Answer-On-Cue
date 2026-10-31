import { http, HttpResponse } from 'msw'
import type { ContentLanguage } from './api'

const NBSP = String.fromCharCode(0xa0)

// No server import here: src/test/msw.ts takes languageHandlers as default handlers.

function language(code: string, bcp47: string, nameEn: string, nameNative: string, quotes: [string, string]): ContentLanguage {
  return {
    code,
    bcp47,
    name_en: nameEn,
    name_native: nameNative,
    quotes,
    enabled: code === 'pl',
    voice_provider: null,
    voice_name: null,
    subject_count: 0,
  }
}

/** What GET /api/languages answers on a fresh install: the seven languages, Polish only enabled. */
export const LANGUAGES: ContentLanguage[] = [
  language('pl', 'pl-PL', 'Polish', 'polski', ['„', '”']),
  language('en', 'en-GB', 'English', 'English', ['“', '”']),
  language('de', 'de-DE', 'German', 'Deutsch', ['„', '“']),
  language('fr', 'fr-FR', 'French', 'français', ['«' + NBSP, NBSP + '»']),
  language('es', 'es-ES', 'Spanish', 'español', ['«', '»']),
  language('it', 'it-IT', 'Italian', 'italiano', ['«', '»']),
  language('uk', 'uk-UA', 'Ukrainian', 'українська', ['«', '»']),
]

/** LANGUAGES with these codes enabled as well. */
export function languagesWith(...codes: string[]): ContentLanguage[] {
  return LANGUAGES.map((row) => (codes.includes(row.code) ? { ...row, enabled: true } : row))
}

export const languageHandlers = [http.get('/api/languages', () => HttpResponse.json(LANGUAGES))]
