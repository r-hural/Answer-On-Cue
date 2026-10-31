/** Word-level differences between two passage texts (a longest common subsequence of words). */

export type DiffKind = 'same' | 'removed' | 'added'

export interface DiffToken {
  /** The word with its punctuation and the whitespace after it. */
  text: string
  kind: DiffKind
}

/** Words with their punctuation and following whitespace attached; leading whitespace joins the first. */
export function words(text: string): string[] {
  const found = text.match(/\S+\s*/gu) ?? []
  const lead = text.match(/^\s+/u)?.[0] ?? ''
  if (lead && found.length > 0) found[0] = lead + found[0]
  return found
}

const core = (token: string) => token.trim()

/**
 * The merged token list: `same` words appear in both texts, `removed` only in the old one and `added`
 * only in the new one. Passages are a few thousand characters at most, so the quadratic table is small.
 */
export function wordDiff(oldText: string, newText: string): DiffToken[] {
  const a = words(oldText)
  const b = words(newText)
  const rows = a.length + 1
  const cols = b.length + 1
  const lcs = new Uint32Array(rows * cols)
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      lcs[i * cols + j] =
        core(a[i]) === core(b[j])
          ? lcs[(i + 1) * cols + j + 1] + 1
          : Math.max(lcs[(i + 1) * cols + j], lcs[i * cols + j + 1])
  const tokens: DiffToken[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (core(a[i]) === core(b[j])) {
      tokens.push({ text: b[j], kind: 'same' })
      i++
      j++
    } else if (lcs[(i + 1) * cols + j] >= lcs[i * cols + j + 1]) {
      tokens.push({ text: a[i++], kind: 'removed' })
    } else {
      tokens.push({ text: b[j++], kind: 'added' })
    }
  }
  while (i < a.length) tokens.push({ text: a[i++], kind: 'removed' })
  while (j < b.length) tokens.push({ text: b[j++], kind: 'added' })
  return tokens
}

/** One side of the comparison: the old text keeps removed words, the new text keeps added ones. */
export function side(tokens: DiffToken[], which: 'old' | 'new'): DiffToken[] {
  const dropped: DiffKind = which === 'old' ? 'added' : 'removed'
  return tokens.filter((token) => token.kind !== dropped)
}
