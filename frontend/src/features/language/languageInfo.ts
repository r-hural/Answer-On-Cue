import type { ContentLanguage } from '../library/api'

const NBSP = String.fromCharCode(0xa0)

export interface LanguageInfo {
  code: string
  bcp47: string
  quotes: [string, string]
}

/** The seven profiles, for the first render and for a failed `GET /api/languages`. */
export const FALLBACK: Record<string, LanguageInfo> = {
  pl: { code: 'pl', bcp47: 'pl-PL', quotes: ['„', '”'] },
  en: { code: 'en', bcp47: 'en-GB', quotes: ['“', '”'] },
  de: { code: 'de', bcp47: 'de-DE', quotes: ['„', '“'] },
  fr: { code: 'fr', bcp47: 'fr-FR', quotes: ['«' + NBSP, NBSP + '»'] },
  es: { code: 'es', bcp47: 'es-ES', quotes: ['«', '»'] },
  it: { code: 'it', bcp47: 'it-IT', quotes: ['«', '»'] },
  uk: { code: 'uk', bcp47: 'uk-UA', quotes: ['«', '»'] },
}

/** The list row, else the fallback, else the bare code with English quotes; no code means Polish (data from before M6). */
export function infoFor(code: string | null | undefined, languages: ContentLanguage[] | null): LanguageInfo {
  const wanted = code ?? 'pl'
  const row = languages?.find((item) => item.code === wanted)
  if (row) return { code: row.code, bcp47: row.bcp47, quotes: row.quotes }
  return FALLBACK[wanted] ?? { code: wanted, bcp47: wanted, quotes: ['“', '”'] }
}
