import { type Topic } from './api'
import { libraryStrings } from './strings'
import { TopicDialog } from './TopicDialog'
import { useContentLanguage } from '../language/ContentLanguage'

const s = libraryStrings.topicEdit

export function RemoveDialog({
  topic,
  busy,
  onCancel,
  onConfirm,
}: {
  topic: Topic
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const cl = useContentLanguage()
  return (
    <TopicDialog titleId="remove-title" onCancel={onCancel}>
      <h2 id="remove-title">
        {s.removeTitle}{' '}
        <span className="content" lang={cl.bcp47}>
          {cl.quote(cl.typeset(topic.title))}
        </span>
        ?
      </h2>
      <p>{s.removeBody(topic.question_count)}</p>
      <p className="small muted">{s.removeHelp}</p>
      <div className="actions">
        <button className="btn" type="button" onClick={onCancel}>
          {s.cancel}
        </button>
        <button className="btn danger solid" type="button" disabled={busy} onClick={onConfirm}>
          {s.removeConfirm}
        </button>
      </div>
    </TopicDialog>
  )
}
