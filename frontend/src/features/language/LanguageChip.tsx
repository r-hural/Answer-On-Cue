import type { ContentLanguage } from '../library/api'

/** The subject's language, shown only once more than one language is enabled. */
export function LanguageChip({ code, languages }: { code: string | null | undefined; languages: ContentLanguage[] | null }) {
  if (!languages || languages.filter((row) => row.enabled).length < 2) return null
  const row = languages.find((item) => item.code === (code ?? 'pl'))
  if (!row) return null
  return (
    <span className="chip" title={row.name_native}>
      {row.name_en}
    </span>
  )
}
