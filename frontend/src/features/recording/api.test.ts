import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../test/msw'
import { analyzeRecording, uploadRecording } from './api'
import { encodeWav } from './pcm'

describe('recording api', () => {
  it('uploads a WAV with its purpose', async () => {
    let body = ''
    server.use(
      http.post('/api/recordings', async ({ request }) => {
        body = await request.text()
        return HttpResponse.json({ id: 7, duration_ms: 1000, purpose: 'mic_check' }, { status: 201 })
      }),
    )
    const result = await uploadRecording(encodeWav(new Float32Array(16000), 16000), 'mic_check')
    expect(result).toEqual({ id: 7, durationMs: 1000 })
    expect(body).toMatch(/name="purpose"\s+mic_check/)
    // jsdom's Blob does not survive undici's FormData, so the file part is checked only by name.
    expect(body).toMatch(/name="file"/)
  })

  it('passes on the backend detail', async () => {
    server.use(
      http.post('/api/recordings', () =>
        HttpResponse.json({ detail: 'The recording is empty.' }, { status: 422 }),
      ),
    )
    await expect(uploadRecording(new Blob([]), 'answer')).rejects.toMatchObject({
      status: 422,
      message: 'The recording is empty.',
    })
  })

  it('falls back to an English message without detail', async () => {
    server.use(http.post('/api/recordings/3/analyze', () => new HttpResponse('boom', { status: 500 })))
    await expect(analyzeRecording(3)).rejects.toThrow('Could not analyse the recording.')
  })

  it('returns the VAD result', async () => {
    const vad = {
      engine: 'webrtcvad-2/30ms',
      speech_intervals: [[1000, 2500]],
      speech_ms: 1500,
      leading_silence_ms: 1000,
      trailing_silence_ms: 500,
      duration_ms: 3000,
    }
    server.use(http.post('/api/recordings/3/analyze', () => HttpResponse.json(vad)))
    expect(await analyzeRecording(3)).toEqual(vad)
  })
})
