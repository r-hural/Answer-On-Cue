/** Polish typography: no line may end with a one-letter word, so the space after it becomes a no-break space. */
export function plNbsp(text: string): string {
  return text.replace(/(?<=^|[\s („"«])([aiouwzAIOUWZ]) /g, '$1 ')
}

/** Content typography by language: the one-letter-word rule is Polish only; null means Polish (data from before M6). */
export function typeset(text: string, code: string | null | undefined): string {
  return code === undefined || code === null || code === 'pl' ? plNbsp(text) : text
}
