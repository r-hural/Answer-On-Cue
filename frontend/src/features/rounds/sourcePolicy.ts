import type { SourcePolicy } from './api'
import { roundStrings } from './strings'

export type Basis = 'source' | 'expanded' | 'mixed'

/** "Only my materials" | "Expand the topic"; an unknown value reads as the strict default. */
export function policyLabel(policy: SourcePolicy | string | undefined): string {
  return policy === 'expanded' ? roundStrings.sources.policy.expanded : roundStrings.sources.policy.materials
}

/** A question's generation policy: "Only my materials" | "My materials + expand" | "Only expand". */
export function questionPolicyLabel(policy: string | undefined): string {
  const labels = roundStrings.sourcesV2.question
  if (policy === 'expanded') return labels.expanded
  if (policy === 'outside') return labels.outside
  return labels.materials
}

/** A round's pool: "My materials" | "All questions" | "My materials + expand" (rounds from before the rework). */
export function roundPolicyLabel(policy: string | undefined): string {
  const labels = roundStrings.sourcesV2.round
  if (policy === 'all') return labels.all
  if (policy === 'expanded') return labels.expanded
  return labels.materials
}

/** "From my materials" | "Expanded" | "Mixed". */
export function basisLabel(basis: Basis | string | undefined): string {
  if (basis === 'expanded') return roundStrings.sources.basis.expanded
  if (basis === 'mixed') return roundStrings.sources.basis.mixed
  return roundStrings.sources.basis.source
}
