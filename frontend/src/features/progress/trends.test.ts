import { describe, expect, it } from 'vitest'
import { getTrends } from './trends'
import { setupTrendsWorld, trendsOut } from './trends.mocks'

describe('trends client', () => {
  it('leaves the defaults out of the query', async () => {
    const world = setupTrendsWorld()
    expect(await getTrends({ subjectId: 3 })).toEqual(trendsOut())
    expect(world.requests).toEqual(['/api/trends?subject_id=3'])
  })

  it('sends every filter that differs from its default', async () => {
    const world = setupTrendsWorld()
    await getTrends({
      subjectId: 3,
      sources: 'expanded',
      series: 'followup',
      style: 'short',
      difficulty: 'apply',
      interrupted: true,
      assisted: true,
    })
    expect(world.requests).toEqual([
      '/api/trends?subject_id=3&source_policy=expanded&series=followup&style=short&difficulty=apply&include_interrupted=true&include_assisted=true',
    ])
  })

  it('rejects with the server message', async () => {
    setupTrendsWorld({ respond: () => 404 })
    await expect(getTrends({ subjectId: 9 })).rejects.toThrow('Trends failed.')
  })
})
