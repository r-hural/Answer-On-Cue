import { useCallback, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { roundStrings } from '../strings'
import { position, type TimelineMark } from './format'

const t = roundStrings.review
const STEP_MS = 5000

export interface AudioPlayerControl {
  /** Callback ref for the audio element. */
  attach(element: HTMLAudioElement | null): void
  playing: boolean
  positionMs: number
  durationMs: number
  toggle(): void
  play(): void
  seek(ms: number): void
  /** Follows the audio element's own clock without seeking it. */
  sync(ms: number): void
  setPlaying(playing: boolean): void
}

function setClock(audio: HTMLAudioElement, seconds: number): void {
  audio.currentTime = seconds
}

/** The state of one recording's playback; the same control drives the player and the timeline marks. */
export function useAudioPlayer(durationMs: number): AudioPlayerControl {
  // the element is state, not a ref, so the handlers below are plain values for rendering
  const [audio, attach] = useState<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [positionMs, setPositionMs] = useState(0)

  const seek = useCallback(
    (ms: number) => {
      const clamped = Math.max(0, Math.min(durationMs, ms))
      if (audio) setClock(audio, clamped / 1000)
      setPositionMs(clamped)
    },
    [audio, durationMs],
  )

  const play = useCallback(() => {
    if (!audio) return
    const started = audio.play() as Promise<void> | undefined
    setPlaying(true)
    if (started && typeof started.catch === 'function') started.catch(() => setPlaying(false))
  }, [audio])

  const toggle = useCallback(() => {
    if (!audio) return
    if (audio.paused) play()
    else {
      audio.pause()
      setPlaying(false)
    }
  }, [audio, play])

  return { attach, playing, positionMs, durationMs, toggle, play, seek, sync: setPositionMs, setPlaying }
}

const PLAY_ICON = (
  <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path d="M6.5 4.5v11l9-5.5z" />
  </svg>
)
const PAUSE_ICON = (
  <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path d="M5.5 4.5h3v11h-3zM11.5 4.5h3v11h-3z" />
  </svg>
)

/** Play, position and a seekable bar in the template's player; the recording itself is a hidden audio element. */
export function AudioPlayer({ player, src, label }: { player: AudioPlayerControl; src: string; label: string }) {
  const { attach, playing, toggle, seek, sync, setPlaying, positionMs, durationMs } = player
  const percent = durationMs > 0 ? Math.round((positionMs / durationMs) * 100) : 0

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target =
      event.key === 'ArrowRight' || event.key === 'ArrowUp'
        ? positionMs + STEP_MS
        : event.key === 'ArrowLeft' || event.key === 'ArrowDown'
          ? positionMs - STEP_MS
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? durationMs
              : null
    if (target === null) return
    event.preventDefault()
    seek(target)
  }

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (box.width > 0) seek(((event.clientX - box.left) / box.width) * durationMs)
  }

  return (
    <div className="player" role="group" aria-label={label}>
      <audio
        ref={attach}
        src={src}
        preload="metadata"
        aria-label={roundStrings.exam.feedback.player}
        onTimeUpdate={(event) => sync(event.currentTarget.currentTime * 1000)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false)
          seek(0)
        }}
      />
      <button className="play" type="button" aria-label={playing ? t.pause : t.play} onClick={toggle}>
        {playing ? PAUSE_ICON : PLAY_ICON}
      </button>
      <span className="time">
        <b>{position(positionMs)}</b> / {position(durationMs)}
      </span>
      <div
        className="scrub"
        role="slider"
        tabIndex={0}
        aria-label={t.position}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={t.positionText(position(positionMs), position(durationMs))}
        onKeyDown={onKeyDown}
        onClick={onClick}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
    </div>
  )
}

/** Long pauses over the answer's length; selecting a time plays from one second before it. */
export function PauseTimeline({
  marks,
  durationMs,
  onSeek,
}: {
  marks: TimelineMark[]
  durationMs: number
  onSeek: (ms: number) => void
}) {
  return (
    <div className="timeline" role="group" aria-label={t.timelineLabel(marks.length)}>
      <div className="tl-track" aria-hidden="true">
        {marks.map((mark) => (
          <span key={mark.startMs} className="tl-pause" style={{ left: `${mark.left}%`, width: `${mark.width}%` }} />
        ))}
      </div>
      <div className="tl-scale">
        <span>0:00</span>
        {marks.map((mark) => (
          <button
            key={mark.startMs}
            className="tl-mark"
            type="button"
            style={{ left: `${mark.center}%` }}
            aria-label={t.playPause(mark.position, mark.length.replace(' s', ' seconds'))}
            onClick={() => onSeek(mark.seekMs)}
          >
            {mark.position}
            <small>{mark.length}</small>
          </button>
        ))}
        <span>{position(durationMs)}</span>
      </div>
      <ul className="tl-legend">
        <li>
          <i className="speech" aria-hidden="true" />
          {t.legendSpeech}
        </li>
        <li>
          <i className="pause" aria-hidden="true" />
          {t.legendPause}
        </li>
        {marks.length > 0 && <li>{t.legendHint}</li>}
      </ul>
    </div>
  )
}
