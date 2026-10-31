import { ApiError } from '../../api/client'
import { recordingStrings } from './strings'

export type RecordingPurpose = 'mic_check' | 'answer'

/** Backend voice-activity result; times in ms. */
export interface VadResult {
  engine: string
  speech_intervals: Array<[number, number]>
  speech_ms: number
  leading_silence_ms: number
  trailing_silence_ms: number
  duration_ms: number
}

/** Throws ApiError with the backend's `detail`, or `fallback` when there is none. */
async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    let detail = fallback
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // not JSON: keep the fallback
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

async function send(path: string, init: RequestInit, fallback: string): Promise<Response> {
  try {
    return await fetch(`/api${path}`, init)
  } catch {
    throw new ApiError(0, fallback)
  }
}

export async function uploadRecording(wav: Blob, purpose: RecordingPurpose): Promise<{ id: number; durationMs: number }> {
  const form = new FormData()
  form.append('file', wav, 'recording.wav')
  form.append('purpose', purpose)
  const fallback = recordingStrings.errors.upload
  const response = await send('/recordings', { method: 'POST', body: form }, fallback)
  const body = await readJson<{ id: number; duration_ms: number }>(response, fallback)
  return { id: body.id, durationMs: body.duration_ms }
}

export async function analyzeRecording(id: number): Promise<VadResult> {
  const fallback = recordingStrings.errors.analyze
  const response = await send(`/recordings/${id}/analyze`, { method: 'POST' }, fallback)
  return readJson<VadResult>(response, fallback)
}

export function recordingAudioUrl(id: number): string {
  return `/api/recordings/${id}/audio`
}
