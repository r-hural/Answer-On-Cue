import { describe, expect, it } from 'vitest'
import { basisLabel, policyLabel, questionPolicyLabel, roundPolicyLabel } from './sourcePolicy'

describe('policyLabel', () => {
  it('names both source policies, strict by default', () => {
    expect(policyLabel('materials')).toBe('Only my materials')
    expect(policyLabel('expanded')).toBe('Expand the topic')
    expect(policyLabel(undefined)).toBe('Only my materials')
  })
})

describe('basisLabel', () => {
  it('names the three labels of a point or question', () => {
    expect(basisLabel('source')).toBe('From my materials')
    expect(basisLabel('expanded')).toBe('Expanded')
    expect(basisLabel('mixed')).toBe('Mixed')
    expect(basisLabel(undefined)).toBe('From my materials')
  })
})

describe('questionPolicyLabel', () => {
  it('names the three generation policies; an unknown value reads as materials', () => {
    expect(questionPolicyLabel('materials')).toBe('Only my materials')
    expect(questionPolicyLabel('expanded')).toBe('My materials + expand')
    expect(questionPolicyLabel('outside')).toBe('Only expand')
    expect(questionPolicyLabel('web')).toBe('Only my materials')
    expect(questionPolicyLabel(undefined)).toBe('Only my materials')
  })
})

describe('roundPolicyLabel', () => {
  it('names the two practice pools and the legacy expanded rounds; an unknown value reads as materials', () => {
    expect(roundPolicyLabel('materials')).toBe('My materials')
    expect(roundPolicyLabel('all')).toBe('All questions')
    expect(roundPolicyLabel('expanded')).toBe('My materials + expand')
    expect(roundPolicyLabel('other')).toBe('My materials')
    expect(roundPolicyLabel(undefined)).toBe('My materials')
  })
})
