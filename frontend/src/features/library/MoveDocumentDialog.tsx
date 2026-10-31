import { useState } from 'react'
import { type Chapter, type Document, type MoveResult, moveDocument } from './api'
import { ChapterName } from './ChapterTabs'
import { libraryStrings } from './strings'
import { TopicDialog } from './TopicDialog'

const c = libraryStrings.chapters

/** Move a document to another chapter of its subject, or to a new one. */
export function MoveDocumentDialog({
  document,
  chapters,
  onCancel,
  onMoved,
}: {
  document: Document
  /** The subject's chapters; the document's own is not offered. Empty for a one-chapter subject. */
  chapters: Chapter[]
  onCancel: () => void
  onMoved: (result: MoveResult) => void
}) {
  const others = chapters.filter((chapter) => chapter.id !== document.chapter_id)
  const [target, setTarget] = useState<number | 'new'>(others[0]?.id ?? 'new')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ready = target !== 'new' || name.trim().length > 0

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      onMoved(
        await moveDocument(document.id, target === 'new' ? { new_chapter_name: name.trim() } : { chapter_id: target }),
      )
    } catch (failure) {
      setError((failure as Error).message)
      setBusy(false)
    }
  }

  return (
    <TopicDialog titleId="move-title" onCancel={onCancel}>
      <h2 id="move-title">{c.moveTitle}</h2>
      <p className="file-name">
        {document.title}
      </p>
      <p className="small muted">{c.moveLead}</p>
      <fieldset className="stack-sm">
        <legend className="label">{c.target}</legend>
        {others.map((chapter) => (
          <label key={chapter.id} className="choice opt">
            <input
              type="radio"
              name="move-target"
              checked={target === chapter.id}
              onChange={() => setTarget(chapter.id)}
            />{' '}
            <strong>
              <ChapterName chapter={chapter} />
            </strong>
          </label>
        ))}
        <label className="choice opt">
          <input type="radio" name="move-target" checked={target === 'new'} onChange={() => setTarget('new')} />{' '}
          <strong>{c.newChapter}</strong>
        </label>
      </fieldset>
      {target === 'new' && (
        <div className="field">
          <label className="label" htmlFor="move-new-name">
            {c.newChapterName}
          </label>
          <input
            className="input content"
            id="move-new-name"
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
      )}
      {error && (
        <p className="small text-error" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button className="btn" type="button" onClick={onCancel}>
          {c.cancel}
        </button>
        <button className="btn primary" type="button" disabled={busy || !ready} onClick={() => void submit()}>
          {c.moveSubmit}
        </button>
      </div>
    </TopicDialog>
  )
}
