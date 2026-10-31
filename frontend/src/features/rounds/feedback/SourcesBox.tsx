import type { ReactNode } from 'react'
import { plNbsp } from '../../../i18n/polish'
import { useContentLanguage } from '../../language/ContentLanguage'
import type { SourceOut } from '../reports'
import { roundStrings } from '../strings'

const t = roundStrings.review

/** One passage: the document and place, with an excerpt. The document title is a name; the place and the excerpt are content. */
export function SourceQuote({ source }: { source: SourceOut }) {
  const cl = useContentLanguage()
  return (
    <div className="source-quote">
      <p className="source">
        <span>{plNbsp(source.document_title)}</span> · <span lang={cl.bcp47}>{cl.typeset(source.locator)}</span>
      </p>
      <p className="content" lang={cl.bcp47}>
        {cl.typeset(source.excerpt)}
      </p>
    </div>
  )
}

/** Where the expected points come from: the document and place, with an excerpt. */
export function SourcesBox({ sources, before }: { sources: SourceOut[]; before?: ReactNode }) {
  if (sources.length === 0 && !before) return null
  return (
    <aside className="side" aria-label={t.sources}>
      {before}
      {sources.length > 0 && (
      <section className="box tint flow" aria-labelledby="h-sources">
        <h2 id="h-sources">{t.sources}</h2>
        {sources.map((source) => (
          <SourceQuote key={source.passage_id} source={source} />
        ))}
      </section>
      )}
    </aside>
  )
}
