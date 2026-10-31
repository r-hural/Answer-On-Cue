import { roundStrings } from '../strings'

const t = roundStrings.exam.level
const MIN_PX = 5
const SPAN_PX = 21
const ON_FROM = 0.25
const SPEAKING_FROM = 0.5

export function LevelBars({ levels, listening }: { levels: number[]; listening: boolean }) {
  const peak = Math.max(...levels)
  const word = !listening ? t.notListening : peak >= SPEAKING_FROM ? t.speaking : t.quiet
  return (
    <div className="level" aria-label={t.label(word)}>
      {word}
      <span aria-hidden="true">
        {levels.map((value, i) => (
          <em key={i} className={listening && value >= ON_FROM ? 'on' : undefined} style={{ height: Math.round(MIN_PX + SPAN_PX * value) }} />
        ))}
      </span>
    </div>
  )
}
