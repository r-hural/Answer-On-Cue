import { useState } from 'react'
import { type SplitPart, type Topic } from './api'
import { libraryStrings } from './strings'
import { TopicDialog } from './TopicDialog'
import { useContentLanguage } from '../language/ContentLanguage'

const s = libraryStrings.topicEdit

export function SplitDialog({
  topic,
  busy,
  onCancel,
  onConfirm,
}: {
  topic: Topic
  busy: boolean
  onCancel: () => void
  onConfirm: (parts: SplitPart[]) => void
}) {
  const cl = useContentLanguage()
  const half = Math.ceil(topic.objectives.length / 2)
  const [names, setNames] = useState(['', ''])
  const [target, setTarget] = useState<Record<number, number>>(() =>
    Object.fromEntries(topic.objectives.map((objective, n) => [objective.id, n < half ? 0 : 1])),
  )
  const enoughObjectives = topic.objectives.length >= 2
  // Each new topic keeps at least one objective, so neither part comes out empty.
  const partsFilled = names.every((_, part) => topic.objectives.some((o) => target[o.id] === part))
  const canSplit = enoughObjectives && partsFilled && names.every((name) => name.trim())

  function submit() {
    onConfirm(
      names.map((name, part) => ({
        title: name.trim(),
        objective_ids: topic.objectives.filter((o) => target[o.id] === part).map((o) => o.id),
        passage_ids: [],
      })),
    )
  }

  return (
    <TopicDialog titleId="split-title" wide onCancel={onCancel}>
      <h2 id="split-title">{s.splitTitle}</h2>
      <p className="content-title" lang={cl.bcp47}>
        {cl.typeset(topic.title)}
      </p>
      <p className="small muted">{s.splitHelp}</p>
      {!enoughObjectives && <p className="small muted">{s.splitNeedTwo}</p>}
      <div className="fields">
        {names.map((name, part) => (
          <div className="field" key={part}>
            <label className="label" htmlFor={`split-name-${part}`}>
              {s.splitName(part + 1)}
            </label>
            <input
              className="input content"
              id={`split-name-${part}`}
              type="text"
              lang={cl.bcp47}
              value={name}
              onChange={(event) =>
                setNames((was) => was.map((item, n) => (n === part ? event.target.value : item)))
              }
            />
          </div>
        ))}
      </div>
      <div className="table-wrap">
        <table className="table middle split-map">
          <thead>
            <tr>
              <th scope="col">{s.splitObjective}</th>
              <th scope="col">{s.splitTarget}</th>
            </tr>
          </thead>
          <tbody>
            {topic.objectives.map((objective) => (
              <tr key={objective.id}>
                <td className="content" lang={cl.bcp47}>
                  {cl.typeset(objective.text)}
                </td>
                <td>
                  <select
                    className="select content"
                    lang={cl.bcp47}
                    aria-label={s.splitTargetFor(objective.text)}
                    value={target[objective.id]}
                    onChange={(event) =>
                      setTarget((was) => ({ ...was, [objective.id]: Number(event.target.value) }))
                    }
                  >
                    {names.map((name, part) => (
                      <option key={part} value={part}>
                        {name.trim() || s.splitName(part + 1)}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {enoughObjectives && !partsFilled && <p className="small muted">{s.splitNeedObjective}</p>}
      <div className="actions">
        <button className="btn" type="button" onClick={onCancel}>
          {s.cancel}
        </button>
        <button className="btn primary" type="button" disabled={busy || !canSplit} onClick={submit}>
          {s.splitConfirm}
        </button>
      </div>
    </TopicDialog>
  )
}
