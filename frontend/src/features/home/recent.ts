import type { RoundListItem } from './api'

export interface SubjectRounds {
  subject: { id: number; name: string }
  rounds: RoundListItem[]
}

export interface RecentRound extends RoundListItem {
  subjectId: number
  subjectName: string
}

export const RECENT_COUNT = 5

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const byNewest = (a: RoundListItem, b: RoundListItem) =>
  Date.parse(b.started_at) - Date.parse(a.started_at)

function allRounds(perSubject: SubjectRounds[]): RecentRound[] {
  return perSubject
    .flatMap(({ subject, rounds }) =>
      rounds.map((round) => ({ ...round, subjectId: subject.id, subjectName: subject.name })),
    )
    .sort(byNewest)
}

/** The newest rounds across subjects, each with its subject. */
export function mergeRecent(perSubject: SubjectRounds[]): RecentRound[] {
  return allRounds(perSubject).slice(0, RECENT_COUNT)
}

/** The newest round that is still running or paused, if any. */
export function unfinished(perSubject: SubjectRounds[]): RecentRound | null {
  return allRounds(perSubject).find(isUnfinished) ?? null
}

export const isUnfinished = (round: { status: string }) =>
  round.status === 'active' || round.status === 'paused'

const two = (n: number) => String(n).padStart(2, '0')

/** "21 Oct" in the browser's time zone. */
export function formatDay(iso: string): string {
  const date = new Date(iso)
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`
}

/** "21 Oct, 19:10" in the browser's time zone. */
export function formatWhen(iso: string): string {
  const date = new Date(iso)
  return `${formatDay(iso)}, ${two(date.getHours())}:${two(date.getMinutes())}`
}

/** The day of the newest round of a subject, or null. */
export function lastPractised(rounds: RoundListItem[]): string | null {
  const newest = [...rounds].sort(byNewest)[0]
  return newest ? formatDay(newest.started_at) : null
}

/** Where a round's date link goes: back into it, or to its results. */
export const roundHref = (round: { id: number; status: string }) =>
  isUnfinished(round) ? `/rounds/${round.id}` : `/rounds/${round.id}/summary`
