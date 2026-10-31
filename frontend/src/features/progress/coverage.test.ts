import { describe, expect, it } from 'vitest'
import { getSubjectCoverage, getTopicCoverage } from './coverage'
import { setupCoverageWorld, subjectCoverage, topicDetail } from './coverage.mocks'

describe('coverage client', () => {
  it('reads the subject coverage and the topic detail', async () => {
    const world = setupCoverageWorld({
      coverage: { 3: subjectCoverage() },
      topics: { 7: topicDetail() },
    })
    expect(await getSubjectCoverage(3)).toEqual(subjectCoverage())
    expect(await getTopicCoverage(7)).toEqual(topicDetail())
    expect(world.requests).toEqual(['/api/coverage/subjects/3', '/api/coverage/topics/7'])
  })

  it('rejects with the server message when the topic is unknown', async () => {
    setupCoverageWorld({ topics: {} })
    await expect(getTopicCoverage(9)).rejects.toThrow('Topic not found.')
  })
})
