import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { typeset } from '../../i18n/polish'
import { listLanguages, type ContentLanguage } from '../library/api'
import { infoFor, type LanguageInfo } from './languageInfo'

export interface ContentLanguageValue extends LanguageInfo {
  /** The list once loaded. */
  languages: ContentLanguage[] | null
  typeset(text: string): string
  quote(text: string): string
}

function build(code: string | null | undefined, languages: ContentLanguage[] | null): ContentLanguageValue {
  const info = infoFor(code, languages)
  return {
    ...info,
    languages,
    typeset: (text) => typeset(text, info.code),
    quote: (text) => info.quotes[0] + text + info.quotes[1],
  }
}

const Context = createContext<ContentLanguageValue>(build('pl', null))

/** Sets the language of the content below it: a subject's or a round's. Polish while the code is unknown. */
export function ContentLanguageProvider({ code, children }: { code: string | null | undefined; children: ReactNode }) {
  const [languages, setLanguages] = useState<ContentLanguage[] | null>(null)

  useEffect(() => {
    let cancelled = false
    listLanguages().then(
      (rows) => {
        if (!cancelled) setLanguages(rows)
      },
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [])

  const value = useMemo(() => build(code, languages), [code, languages])
  return <Context.Provider value={value}>{children}</Context.Provider>
}

/** Polish without a provider. */
export function useContentLanguage(): ContentLanguageValue {
  return useContext(Context)
}

/** A provider for a page that learns the language only once it has loaded its subject: the page reports it with `setCode`. */
export function ContentLanguageHost({ children }: { children: (setCode: (code: string | null) => void) => ReactNode }) {
  const [code, setCode] = useState<string | null>(null)
  return <ContentLanguageProvider code={code}>{children(setCode)}</ContentLanguageProvider>
}
