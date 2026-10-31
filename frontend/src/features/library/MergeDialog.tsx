import { useState } from 'react'
import { type Topic } from './api'
import { libraryStrings } from './strings'
import { TopicDialog } from './TopicDialog'
import { useContentLanguage } from '../language/ContentLanguage'

const s = libraryStrings.topicEdit

export function MergeDialog({
  topics,
  busy,
  onCancel,
  onConfirm,
}: {
  topics: Topic[]
  busy: boolean
  onCancel: () => void
  onConfirm: (title: string) => void
}) {
  const cl = useContentLanguage()
  const [title, setTitle] = useState(topics[0]?.title ?? '')
  return (
    <TopicDialog titleId="merge-title" onCancel={onCancel}>
      <h2 id="merge-title">{s.mergeTitle(topics.length)}</h2>
      <ul className="rows compact">
        {topics.map((topic) => (
          <li key={topic.id}>
            <span className="content" lang={cl.bcp47}>
              {cl.typeset(topic.title)}
            </span>
            <span className="muted small num">
              {s.objectives(topic.objectives.length)}, {s.questions(topic.question_count)}
            </span>
          </li>
        ))}
      </ul>
      <div className="field">
        <label className="label" htmlFor="merge-name">
          {s.mergeName}
        </label>
        <textarea
          className="textarea content name-edit"
          id="merge-name"
          lang={cl.bcp47}
          rows={1}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <p className="small muted">{s.mergeHelp}</p>
      <div className="actions">
        <button className="btn" type="button" onClick={onCancel}>
          {s.cancel}
        </button>
        <button
          className="btn primary"
          type="button"
          disabled={busy || !title.trim()}
          onClick={() => onConfirm(title.trim())}
        >
          {s.mergeConfirm}
        </button>
      </div>
    </TopicDialog>
  )
}
