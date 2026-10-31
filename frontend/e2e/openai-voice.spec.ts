import { expect, type Page, test } from '@playwright/test'

interface Voice {
  provider: string
  id: string
  installed: boolean
}

// The e2e server may or may not hold an OpenAI key; both cases are checked. The shared e2e database is
// never changed: PUT /api/languages/* is fulfilled here, and PUT /api/settings is never sent.
test.describe('Languages settings and the OpenAI Realtime voice', () => {
  const seen: { method: string; url: string }[] = []

  test.beforeEach(({ page }) => {
    seen.length = 0
    page.on('request', (request) => seen.push({ method: request.method(), url: request.url() }))
  })

  test.afterEach(() => {
    // nothing reaches the network but a click on "Check for more voices", and no click does it here
    expect(seen.filter((request) => request.url.includes('refresh=true'))).toEqual([])
    expect(seen.filter((request) => request.method === 'PUT' && request.url.includes('/api/settings'))).toEqual([])
  })

  async function openLanguages(page: Page): Promise<Voice[]> {
    const listed = page.waitForResponse(
      (response) => response.url().endsWith('/api/voice/voices?language=pl') && response.status() === 200,
    )
    await page.goto('/settings#languages')
    const voices = (await (await listed).json()) as Voice[]
    const polish = page.locator('section#languages').getByRole('listitem').first()
    await expect(polish.getByRole('radio', { name: 'OpenAI Realtime', exact: true })).toBeEnabled()
    return voices
  }

  test('lists the languages and the Polish OpenAI voices', async ({ page }) => {
    const voices = await openLanguages(page)
    const section = page.locator('section#languages')
    await expect(section.getByRole('list', { name: 'Content languages' }).getByRole('listitem')).toHaveCount(7)
    await expect(section.getByRole('checkbox', { name: 'Enable Polish', exact: true })).toBeChecked()
    const polish = section.getByRole('listitem').first()
    const openai = voices.filter((voice) => voice.provider === 'openai')
    expect(openai).toHaveLength(10)
    await polish.getByRole('radio', { name: 'OpenAI Realtime', exact: true }).check()
    await expect(polish.locator('select#voice-pl option')).toHaveCount(10)
    const listen = polish.getByRole('button', { name: 'Listen', exact: true })
    if (openai.every((voice) => !voice.installed)) {
      test.info().annotations.push({ type: 'case', description: 'no OpenAI key on the e2e server' })
      await expect(polish.getByText('First add an OpenAI API key under API keys.', { exact: true })).toBeVisible()
      await expect(listen).toBeDisabled()
    } else {
      test.info().annotations.push({ type: 'case', description: 'an OpenAI key on the e2e server' })
      await expect(listen).toBeEnabled()
    }
  })

  test('saves an OpenAI voice for Polish without reaching the server', async ({ page }) => {
    let languages: Record<string, unknown>[] | null = null
    const sent: { url: string; body: Record<string, unknown> }[] = []
    await page.route('**/api/languages**', async (route) => {
      if (route.request().method() === 'GET') {
        if (languages === null) languages = (await (await route.fetch()).json()) as Record<string, unknown>[]
        return route.fulfill({ json: languages })
      }
      const body = route.request().postDataJSON() as Record<string, unknown>
      sent.push({ url: route.request().url(), body })
      const code = new URL(route.request().url()).pathname.split('/').pop()
      languages = (languages ?? []).map((row) => (row.code === code ? { ...row, ...body } : row))
      return route.fulfill({ json: languages.find((row) => row.code === code) })
    })
    await openLanguages(page)
    const polish = page.locator('section#languages').getByRole('listitem').first()
    await polish.getByRole('radio', { name: 'OpenAI Realtime', exact: true }).check()
    const select = polish.locator('select#voice-pl')
    await expect(select).toBeEnabled()
    await select.selectOption({ label: (await select.locator('option', { hasText: /^Cedar \(/ }).textContent()) ?? '' })
    const save = polish.getByRole('button', { name: 'Save voice', exact: true })
    await expect(save).toBeEnabled()
    await save.click()
    await expect(polish.getByText('Voice saved.', { exact: true })).toBeVisible()
    expect(sent).toHaveLength(1)
    expect(new URL(sent[0].url).pathname).toBe('/api/languages/pl')
    expect(sent[0].body).toEqual({ voice_provider: 'openai', voice_name: 'cedar' })

    await page.reload()
    const again = page.locator('section#languages').getByRole('listitem').first()
    await expect(again.getByRole('radio', { name: 'OpenAI Realtime', exact: true })).toBeChecked()
    await expect(again.locator('select#voice-pl')).toHaveValue('cedar')
    expect(seen.filter((request) => request.method === 'PUT' && request.url.includes('/api/languages'))).toHaveLength(1)
  })
})
