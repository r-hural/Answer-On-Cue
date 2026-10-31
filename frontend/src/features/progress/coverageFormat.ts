import { seconds } from '../rounds/feedback/format'
import type { CoverageStatus } from './coverage'
import { progressStrings } from './strings'

/** Display order of the counts: the best status first. */
export const STATUSES: CoverageStatus[] = ['strong', 'improving', 'needs_work', 'not_practised']

/** Order of the key line, as in the template. */
export const KEY_ORDER: CoverageStatus[] = ['not_practised', 'needs_work', 'improving', 'strong']

const CLASS: Record<CoverageStatus, string> = {
  not_practised: 'none',
  needs_work: 'needs',
  improving: 'improving',
  strong: 'strong',
}

/** The `.status` colour class of a status. */
export function statusClass(status: CoverageStatus): string {
  return CLASS[status]
}

/** "51 · 44 · 60", or a dash when there is no independent answer yet. */
export function recentFactual(scores: number[]): string {
  return scores.length === 0 ? progressStrings.dash : scores.map((value) => Math.round(value)).join(' · ')
}

/** "Prep. 8 s"; a dash, never "0 s", when nothing was measured. */
export function prep(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? progressStrings.dash : `${progressStrings.coverage.prep} ${seconds(ms)}`
}
