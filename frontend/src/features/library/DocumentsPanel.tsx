import { EstimateLine } from '../costs/EstimateLine'
import { useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { type Chapter, type Document, type Job, EXTRACT_KIND } from './api'
import { ChapterName } from './ChapterTabs'
import { awaitsQuestions } from './chapters'
import { UploadIcon } from './UploadIcon'
import { isLive } from './useJobs'
import { libraryStrings } from './strings'

const s = libraryStrings.subject
const c = libraryStrings.chapters

export interface ChapterFieldProps {
  value: string
  onChange: (value: string) => void
  /** Names offered in the list: the subject's named chapters. */
  names: string[]
  /** Where an empty field puts the document; only for a subject with two or more chapters. */
  defaultChapter: Chapter | null
}

/** The optional chapter of the next upload: an existing chapter's name, a new name, or empty. */
function ChapterField({ value, onChange, names, defaultChapter }: ChapterFieldProps) {
  return (
    <div className="field mt-4">
      <label className="label" htmlFor="upload-chapter">
        {c.uploadField}
      </label>
      <input
        className="input content"
        id="upload-chapter"
        list={names.length > 0 ? 'upload-chapters' : undefined}
        maxLength={200}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {names.length > 0 && (
        <datalist id="upload-chapters">
          {names.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      )}
      {defaultChapter && (
        <p className="hint">
          {c.uploadHint.before}
          <ChapterName chapter={defaultChapter} />
          {c.uploadHint.after}
        </p>
      )}
    </div>
  )
}

const statusChip: Record<string, string> = {
  imported: 'chip ok',
  failed: 'chip error',
  processing: 'chip accent',
}

export function DropZone({
  uploading,
  onFile,
}: {
  uploading: boolean
  onFile: (file: File) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const open = () => !uploading && input.current?.click()
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      open()
    }
  }
  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setOver(false)
    const file = event.dataTransfer.files[0]
    if (file && !uploading) onFile(file)
  }

  return (
    <div
      className={over ? 'drop over' : 'drop'}
      role="button"
      tabIndex={0}
      aria-disabled={uploading}
      onClick={open}
      onKeyDown={onKey}
      onDragOver={(event) => {
        event.preventDefault()
        if (!uploading) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <UploadIcon />
      <p>{uploading ? s.uploading : s.dropZone}</p>
      <input
        ref={input}
        type="file"
        aria-label={s.fileInput}
        accept=".pdf,.docx,.txt,.md"
        hidden
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) onFile(file)
        }}
      />
    </div>
  )
}

export function DocumentsPanel({
  documents,
  jobs,
  selected,
  canRun,
  uploading,
  uploadError,
  onFile,
  onOpen,
  onExtract,
  chapterField,
  onMove,
  generateHref,
  replaceHref,
}: {
  documents: Document[]
  jobs: Job[]
  selected: number | null
  canRun: boolean
  uploading: boolean
  uploadError: string | null
  onFile: (file: File) => void
  onOpen: (versionId: number) => void
  onExtract: (versionId: number) => void
  chapterField: ChapterFieldProps
  onMove: (document: Document) => void
  /** Where "Generate questions" of a version leads: the Generate panel with that book chosen. */
  generateHref: (versionId: number) => string
  /** Where a row's "Replace" leads: the document's new-version page. Without it, no link. */
  replaceHref?: (documentId: number) => string
}) {
  const liveFor = (versionId: number) =>
    jobs.some((job) => job.kind === EXTRACT_KIND && job.document_version_id === versionId && isLive(job))
  const empty = documents.length === 0

  return (
    <section id="documents" aria-labelledby="documents-title">
      <div className="section-head">
        <h2 id="documents-title">{empty ? s.firstTitle : s.documents}</h2>
        <span className="muted small">{s.count(documents.length)}</span>
      </div>
      {empty ? (
        <p className="lead">{s.firstLead}</p>
      ) : (
        <ul className="rows stack-sm">
          {documents.map((doc) => {
            const version = doc.latest_version
            return (
              <li key={doc.id} aria-current={selected === version.id}>
                <div>
                  <span className="title file-name">
                    {doc.title}
                  </span>
                  <p className="meta num">
                    <span className={statusChip[version.status] ?? 'chip'}>
                      {s.status[version.status] ?? version.status}
                    </span>
                    <span>{s.chars(version.char_count)}</span>
                    {version.page_count ? <span>{s.pages(version.page_count)}</span> : null}
                    <span>{s.passages(version.passage_count)}</span>
                    {version.status === 'imported' && !version.topics_extracted_at && (
                      <EstimateLine extraction={version.id} inline />
                    )}
                  </p>
                  {version.message && (
                    <p className="small text-error mt-1" role="alert">
                      {version.message}
                    </p>
                  )}
                </div>
                <div className="end">
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => onOpen(version.id)}
                    disabled={version.status !== 'imported'}
                    aria-label={`${s.open} ${doc.title}`}
                  >
                    {s.open}
                  </button>
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => onExtract(version.id)}
                    disabled={!canRun || version.status !== 'imported' || liveFor(version.id)}
                    aria-label={`${libraryStrings.topics.extract}: ${doc.title}`}
                  >
                    {libraryStrings.topics.extract}
                  </button>
                  {awaitsQuestions(doc) && (
                    <Link
                      className="btn small"
                      to={generateHref(version.id)}
                      aria-label={c.generateLinkLabel(doc.title)}
                    >
                      {c.generateLink}
                    </Link>
                  )}
                  {replaceHref && (
                    <Link
                      className="btn small"
                      to={replaceHref(doc.id)}
                      aria-label={libraryStrings.versions.replaceLabel(doc.title)}
                    >
                      {libraryStrings.versions.replaceShort}
                    </Link>
                  )}
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => onMove(doc)}
                    aria-label={c.moveLabel(doc.title)}
                  >
                    {c.move}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
      <ChapterField {...chapterField} />
      <DropZone uploading={uploading} onFile={onFile} />
      {uploadError && (
        <p className="small text-error mt-2" role="alert">
          {uploadError}
        </p>
      )}
      {empty && <p className="hint mt-3">{s.formats}</p>}
    </section>
  )
}
