import { describe, expect, it } from 'vitest'

// The project has no Node type definitions: load the Node modules by a computed name and type what is used.
interface NodeFs {
  readFileSync(path: string, encoding: 'utf8'): string
}
const nodeFs = 'node:fs'
const cwd = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()

describe('content font stack', () => {
  it('keeps a serif with Cyrillic ahead of the generic family', async () => {
    const fs = (await import(/* @vite-ignore */ nodeFs)) as NodeFs
    const css = fs.readFileSync(`${cwd}/src/styles/tokens.css`, 'utf8')
    const stack = /--font-content:\s*([^;]+);/.exec(css)?.[1] ?? ''
    const georgia = stack.indexOf('Georgia')
    const times = stack.search(/"Times New [A-Za-z]+"/)
    expect(georgia).toBeGreaterThan(-1)
    expect(times).toBeGreaterThan(-1)
    expect(georgia).toBeLessThan(stack.lastIndexOf('serif'))
    expect(times).toBeLessThan(stack.lastIndexOf('serif'))
  })
})
