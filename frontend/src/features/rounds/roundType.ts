import type { RoundType } from './api'
import { roundStrings } from './strings'

/** The round type's label in headers and meta lines; old rounds and rows are Standard. */
export function roundTypeLabel(type: RoundType | string | undefined): string {
  const labels = roundStrings.adaptive.types
  if (type === 'adaptive') return labels.adaptive
  if (type === 'extension') return labels.extension
  if (type === 'retry') return roundStrings.repetition.roundType
  return labels.standard
}
