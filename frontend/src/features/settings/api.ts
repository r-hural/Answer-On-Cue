import { apiGet, apiSend } from '../../api/client'
import type { ContentLanguage } from '../library/api'

export type TextProvider = 'openai' | 'anthropic'
export type VoiceProvider = 'piper' | 'azure_speech' | 'browser' | 'openai'

export interface Preferences {
  transcription_model: string
  generation_provider: TextProvider
  generation_model: string
  assessment_provider: TextProvider
  assessment_model: string
  voice_provider: VoiceProvider
  voice_name: string
  voice_rate: number
  factual_weights: [number, number]
  factual_band_bounds: [number, number, number]
  round_question_count: number
  round_prep_seconds: number
  round_answer_seconds: number
  spending_ceiling_usd?: number // 0: no ceiling
}

export interface VoiceInfo {
  provider: VoiceProvider
  id: string
  label: string
  installed: boolean
  gender: string | null
  /** The language code for Piper and Azure voices; null for OpenAI and the browser voice. */
  language?: string | null
}

export interface DownloadState {
  voice: string
  status: 'idle' | 'running' | 'done' | 'failed'
  bytes_done: number
  bytes_total: number | null
  error: string | null
}

export interface KeyTestResult {
  ok: boolean
  error_kind: string | null
  message: string
}

export const fetchPreferences = () => apiGet<Preferences>('/settings')
export const savePreferences = (patch: Partial<Preferences>) =>
  apiSend<Preferences>('PUT', '/settings', patch)
/** refresh is the only call that may reach the network; it is sent only together with a language. */
export const fetchVoices = (language?: string, options: { refresh?: boolean } = {}) => {
  const query = language
    ? `?language=${encodeURIComponent(language)}${options.refresh ? '&refresh=true' : ''}`
    : ''
  return apiGet<VoiceInfo[]>(`/voice/voices${query}`)
}
export const startDownload = (voice: string) =>
  apiSend<DownloadState>('POST', '/voice/downloads', { voice })
export const fetchDownload = (voice: string) =>
  apiGet<DownloadState>(`/voice/downloads/${encodeURIComponent(voice)}`)
export const previewVoice = (
  provider: VoiceProvider,
  voice: string,
  rate: number,
  language?: string,
) =>
  apiSend<{ audio_url: string | null }>('POST', '/voice/preview', {
    provider,
    voice,
    rate,
    ...(language ? { language } : {}),
  })

export interface LanguagePatch {
  enabled?: boolean
  voice_provider?: VoiceProvider | null
  voice_name?: string | null
}

export const updateLanguage = (code: string, patch: LanguagePatch) =>
  apiSend<ContentLanguage>('PUT', `/languages/${encodeURIComponent(code)}`, patch)
