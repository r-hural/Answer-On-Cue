import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { fetchProvidersStatus, hasGenerationKey } from '../../api/providers'
import {
  type Chapter,
  type Document,
  type GenerateOptions,
  type MoveResult,
  type Subject,
  type Term,
  type Topic,
  countQuestions,
  deleteChapter,
  getSubject,
  listChapters,
  listDocuments,
  listTerms,
  listTopics,
  renameChapter,
  startExtraction,
  startGeneration,
  uploadDocument,
  GENERATE_KIND,
} from './api'
import { ContentLanguageHost, useContentLanguage } from '../language/ContentLanguage'
import { LanguageChip } from '../language/LanguageChip'
import { ChapterTabs } from './ChapterTabs'
import { documentsIn, isChaptered } from './chapters'
import { DeleteSubject } from './DeleteSubject'
import { DocumentDetail } from './DocumentDetail'
import { DocumentsPanel } from './DocumentsPanel'
import { JobsPanel } from './JobsPanel'
import { MoveDocumentDialog } from './MoveDocumentDialog'
import { CoverageBox } from '../progress/CoverageBox'
import { Notice } from '../../ui/Notice'
import { libraryStrings } from './strings'
import { BankPanel, TopicsPanel } from './TopicsBankPanel'
import { isLive, useJobs } from './useJobs'
import { plNbsp } from '../../i18n/polish'

const c = libraryStrings.chapters

interface Results {
  topics: Topic[]
  terms: Term[]
  count: number
}

async function fetchResults(subjectId: number, chapterId: number | null): Promise<Results> {
  const [topics, terms, count] = await Promise.all([
    listTopics(subjectId, chapterId),
    listTerms(subjectId),
    countQuestions(subjectId, chapterId),
  ])
  return { topics, terms, count }
}

interface Structure {
  subject: Subject
  documents: Document[]
  /** Loaded only for a subject with two or more chapters. */
  chapters: Chapter[]
}

async function fetchStructure(subjectId: number): Promise<Structure> {
  const [subject, documents] = await Promise.all([getSubject(subjectId), listDocuments(subjectId)])
  const chapters = isChaptered(subject) ? await listChapters(subjectId) : []
  return { subject, documents, chapters }
}

/** Rename the current chapter in place; the first chapter may be named too. */
function RenameChapter({ chapter, onRenamed }: { chapter: Chapter; onRenamed: (name: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const wasEditing = useRef(false)
  // the field takes focus when the form opens; focus returns to the button when it closes
  useEffect(() => {
    if (editing) inputRef.current?.focus()
    else if (wasEditing.current) buttonRef.current?.focus()
    wasEditing.current = editing
  }, [editing])
  if (!editing)
    return (
      <button
        ref={buttonRef}
        className="btn small"
        type="button"
        onClick={() => {
          setName(chapter.name ?? '')
          setError(null)
          setEditing(true)
        }}
      >
        {c.rename}
      </button>
    )
  return (
    <form
      className="actions"
      onSubmit={async (event) => {
        event.preventDefault()
        try {
          await renameChapter(chapter.id, name.trim())
          setEditing(false)
          onRenamed(name.trim())
        } catch (failure) {
          setError((failure as Error).message)
        }
      }}
    >
      <label className="label" htmlFor="chapter-rename">
        {c.renameName}
      </label>
      <input
        className="input content"
        id="chapter-rename"
        ref={inputRef}
        maxLength={200}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <button className="btn small" type="submit" disabled={!name.trim()}>
        {c.renameSave}
      </button>
      <button className="btn small" type="button" onClick={() => setEditing(false)}>
        {c.cancel}
      </button>
      {error && (
        <p className="small text-error" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}

export function SubjectPage() {
  return <ContentLanguageHost>{(setCode) => <SubjectView onLanguage={setCode} />}</ContentLanguageHost>
}

function SubjectView({ onLanguage }: { onLanguage: (code: string | null) => void }) {
  const cl = useContentLanguage()
  const subjectId = Number(useParams().subjectId)
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [subject, setSubject] = useState<Subject | null>(null)
  const language = subject?.language ?? null
  useLayoutEffect(() => onLanguage(language), [onLanguage, language])
  const [documents, setDocuments] = useState<Document[]>([])
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [terms, setTerms] = useState<Term[]>([])
  const [questionCount, setQuestionCount] = useState(0)
  const [canRun, setCanRun] = useState<boolean | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  // ?document=<id> (back from replacing it) opens that document's latest version
  const openDocument = Number(searchParams.get('document')) || null
  const [openedFor, setOpenedFor] = useState<number | null>(null)
  const [uploadingName, setUploadingName] = useState<string | null>(null)
  const uploading = uploadingName !== null
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [moving, setMoving] = useState<Document | null>(null)
  const [moved, setMoved] = useState<MoveResult | null>(null)
  const [chapterError, setChapterError] = useState<string | null>(null)

  // chapters show only from the second one on; with one, nothing asks or sends a chapter
  const chaptered = isChaptered(subject) && chapters.length >= 2
  const wanted = Number(searchParams.get('chapter'))
  const current = chaptered ? (chapters.find((chapter) => chapter.id === wanted) ?? chapters[0]) : null
  const chapterId = current?.id ?? null
  const chapterRef = useRef(chapterId)
  useEffect(() => {
    chapterRef.current = chapterId
  })

  const [chapterName, setChapterName] = useState('')
  const [nameFor, setNameFor] = useState<number | null>(null)
  if (nameFor !== chapterId) {
    setNameFor(chapterId)
    setChapterName(current?.name ?? '')
  }

  /** Which chapter the loaded topics belong to; another chapter's topics are never shown or sent. */
  const [resultsFor, setResultsFor] = useState<string | null>(null)
  const applyResults = useCallback((results: Results, key: string) => {
    setResultsFor(key)
    setTopics(results.topics)
    setTerms(results.terms)
    setQuestionCount(results.count)
  }, [])
  const refreshResults = useCallback(
    () => {
      const chapter = chapterRef.current
      return fetchResults(subjectId, chapter).then((results) => applyResults(results, `${subjectId}:${chapter}`))
    },
    [subjectId, applyResults],
  )
  const applyStructure = useCallback((structure: Structure) => {
    setSubject(structure.subject)
    setDocuments(structure.documents)
    setChapters(structure.chapters)
  }, [])
  const refreshStructure = useCallback(
    () => fetchStructure(subjectId).then(applyStructure),
    [subjectId, applyStructure],
  )

  const { jobs, attach, resume } = useJobs(subjectId, () => {
    // the document flags (topics extracted, questions generated) change too
    refreshStructure()
      .then(() => refreshResults())
      .catch(() => undefined)
  })

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchStructure(subjectId), fetchProvidersStatus()])
      .then(([structure, status]) => {
        if (cancelled) return
        applyStructure(structure)
        setCanRun(hasGenerationKey(status))
      })
      .catch((failure: Error) => !cancelled && setError(failure.message))
    return () => {
      cancelled = true
    }
  }, [subjectId, applyStructure])

  const ready = subject !== null && subject.id === subjectId
  useEffect(() => {
    if (!ready) return
    let cancelled = false
    fetchResults(subjectId, chapterId)
      .then((results) => !cancelled && applyResults(results, `${subjectId}:${chapterId}`))
      .catch((failure: Error) => !cancelled && setError(failure.message))
    return () => {
      cancelled = true
    }
  }, [ready, subjectId, chapterId, applyResults])

  // ?generate=<version id> or ?generate=all opens the Generate panel with that choice
  const generateParam = searchParams.get('generate')
  const preset = generateParam === 'all' ? 'all' : Number(generateParam) || null
  useEffect(() => {
    if (ready && generateParam !== null) document.getElementById('questions')?.scrollIntoView?.({ block: 'start' })
  }, [ready, generateParam])
  const generateHref = (versionId: number) =>
    `?${chapterId === null ? '' : `chapter=${chapterId}&`}generate=${versionId}`

  const showChapter = (id: number | null) =>
    setSearchParams(id === null ? {} : { chapter: String(id) }, { replace: true })

  async function upload(file: File) {
    setUploadingName(file.name)
    setUploadError(null)
    try {
      const name = chapterName.trim()
      const document = await uploadDocument(subjectId, file, name ? { name } : undefined)
      const structure = await fetchStructure(subjectId)
      applyStructure(structure)
      if (isChaptered(structure.subject) && document.chapter_id != null && document.chapter_id !== chapterId)
        showChapter(document.chapter_id)
      if (document.latest_version.status === 'imported') setSelected(document.latest_version.id)
    } catch (failure) {
      setUploadError((failure as Error).message)
    } finally {
      setUploadingName(null)
    }
  }

  async function start(call: () => Promise<{ job_id: number }>) {
    setActionError(null)
    try {
      await attach((await call()).job_id)
    } catch (failure) {
      setActionError((failure as Error).message)
    }
  }

  const generate = (options: GenerateOptions) => start(() => startGeneration(subjectId, options))
  const extract = (versionId: number) => start(() => startExtraction(versionId))

  function afterMove(result: MoveResult) {
    setMoving(null)
    setMoved(result)
    refreshStructure()
      .then(() => refreshResults())
      .catch((failure: Error) => setActionError(failure.message))
  }

  async function removeChapter(chapter: Chapter) {
    setChapterError(null)
    try {
      await deleteChapter(chapter.id)
      showChapter(null)
      await refreshStructure()
    } catch (failure) {
      setChapterError((failure as Error).message)
    }
  }

  if (error)
    return (
      <Notice kind="error">
        <p>{error}</p>
      </Notice>
    )
  if (!subject || canRun === null) return <p className="muted">{libraryStrings.loading}</p>

  if (openDocument !== null && openedFor !== openDocument) {
    const found = documents.find((d) => d.id === openDocument)
    if (found) {
      setOpenedFor(openDocument)
      setSelected(found.latest_version.id)
    }
  }
  const selectedDocument = documents.find((d) => d.latest_version.id === selected)
  const reviewCount = subject.source_review_count ?? 0

  const generating = jobs.some((job) => job.kind === GENERATE_KIND && isLive(job))
  const live = jobs.filter(isLive)
  const shown = documentsIn(documents, chapterId)
  // until the current chapter's results arrive, the previous chapter's topics are hidden
  const shownTopics = resultsFor === `${subjectId}:${chapterId}` ? topics : []
  const deletable =
    current !== null && !current.is_default && current.document_count === 0 && current.topic_count === 0
  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/library">{libraryStrings.subject.back}</Link> <span aria-hidden="true">›</span>{' '}
        <span className="content">
          {subject.name}
        </span>
      </nav>
      <div className="page-head">
        <div className="min-w-0">
          <h1 className="content">
            {subject.name}
          </h1>
          <LanguageChip code={subject.language} languages={cl.languages} />
          {subject.description && (
            <p className="content">
              {plNbsp(subject.description)}
            </p>
          )}
        </div>
      </div>

      {chaptered && current && (
        <>
          <ChapterTabs chapters={chapters} current={current.id} />
          {chapters[0].name === null && <p className="hint mb-4">{c.nameFirst}</p>}
          <div className="actions mb-8">
            <RenameChapter
              key={current.id}
              chapter={current}
              onRenamed={(name) => {
                setChapterName(name)
                refreshStructure().catch(() => undefined)
              }}
            />
            {deletable && (
              <button className="btn small" type="button" onClick={() => void removeChapter(current)}>
                {c.delete}
              </button>
            )}
          </div>
          {chapterError && (
            <Notice kind="error" className="mb-8">
              <p>{chapterError}</p>
            </Notice>
          )}
        </>
      )}

      {!canRun && (
        <Notice kind="warn" className="mb-8">
          <p>
            {libraryStrings.noKey} <Link to="/settings">{libraryStrings.settingsLink}</Link>
          </p>
        </Notice>
      )}
      {uploadingName && (
        <Notice kind="info" className="mb-8">
          <strong>
            {libraryStrings.subject.uploadingFile(uploadingName)}
          </strong>
          <p>{libraryStrings.subject.uploadingHelp}</p>
        </Notice>
      )}
      {moved && (
        <Notice kind="ok" className="mb-8">
          <p role="status">{c.moved(moved.topics_moved.length)}</p>
        </Notice>
      )}
      {live.map((job) => (
        <Notice key={job.id} kind="info" className="mb-8">
          <strong className="num">
            {libraryStrings.jobs.progress(
              libraryStrings.jobs.kinds[job.kind] ?? job.kind,
              job.steps_done,
              Math.max(job.steps_total, 1),
            )}
          </strong>
          <p>{libraryStrings.jobs.background}</p>
        </Notice>
      ))}
      {actionError && (
        <Notice kind="error" className="mb-8">
          <p>{actionError}</p>
        </Notice>
      )}

      <div className="cols-main">
        <div>
          <DocumentsPanel
            documents={shown}
            jobs={jobs}
            selected={selected}
            canRun={canRun}
            uploading={uploading}
            uploadError={uploadError}
            onFile={upload}
            onOpen={setSelected}
            onExtract={extract}
            chapterField={{
              value: chapterName,
              onChange: setChapterName,
              names: chapters.flatMap((chapter) => (chapter.name === null ? [] : [chapter.name])),
              defaultChapter: chaptered ? chapters[0] : null,
            }}
            onMove={(document) => {
              setMoved(null)
              setMoving(document)
            }}
            generateHref={generateHref}
            replaceHref={(id) => `/library/${subjectId}/documents/${id}/new-version`}
          />
          {selected !== null && (
            <DocumentDetail key={selected} versionId={selected} documentId={selectedDocument?.id} />
          )}
          <TopicsPanel subjectId={subjectId} topics={shownTopics} terms={terms} chapterId={chapterId} />
          <BankPanel
            subjectId={subjectId}
            reviewCount={reviewCount}
            topics={shownTopics}
            hasTopics={shownTopics.length > 0}
            questionCount={questionCount}
            canRun={canRun}
            busy={generating}
            onGenerate={generate}
            documents={shown}
            chapterId={chapterId}
            preset={preset}
          />
        </div>
        <aside className="side" aria-label={libraryStrings.jobs.side}>
          <JobsPanel jobs={jobs} onResume={resume} />
          <CoverageBox subjectId={subjectId} chapterId={chapterId} />
          <DeleteSubject
            subjectId={subjectId}
            name={subject.name}
            onDeleted={() => navigate('/library')}
          />
        </aside>
      </div>
      {moving && (
        <MoveDocumentDialog
          document={moving}
          chapters={chaptered ? chapters : []}
          onCancel={() => setMoving(null)}
          onMoved={afterMove}
        />
      )}
    </>
  )
}
