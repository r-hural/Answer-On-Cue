import { Link } from 'react-router-dom'
import { plNbsp } from '../../i18n/polish'
import type { Chapter } from './api'
import { libraryStrings } from './strings'

const c = libraryStrings.chapters

/** A chapter's name as Polish content, or the English "Unnamed chapter" for the unnamed first one. */
export function ChapterName({ chapter }: { chapter: Pick<Chapter, 'name'> }) {
  if (chapter.name === null) return <>{c.unnamed}</>
  return (
    <span className="content">
      {plNbsp(chapter.name)}
    </span>
  )
}

/** The chapter navigation of a subject with two or more chapters; `?chapter=<id>` keeps the choice in the URL. */
export function ChapterTabs({
  chapters,
  current,
  hrefFor = (id) => `?chapter=${id}`,
}: {
  chapters: Chapter[]
  current: number | null
  hrefFor?: (id: number) => string
}) {
  return (
    <nav className="tabs" aria-label={c.tabsLabel}>
      {chapters.map((chapter) => (
        <Link key={chapter.id} to={hrefFor(chapter.id)} aria-current={chapter.id === current ? 'page' : undefined}>
          <ChapterName chapter={chapter} />
        </Link>
      ))}
    </nav>
  )
}
