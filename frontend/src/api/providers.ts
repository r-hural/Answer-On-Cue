import { apiGet } from './client'

export type Provider = 'openai' | 'anthropic' | 'azure_speech'

export interface KeyStatus {
  provider: Provider
  configured: boolean
  masked: string | null
  region: string | null
}

export interface RoleStatus {
  provider: string
  model: string
}

export interface ProvidersStatus {
  keys: KeyStatus[]
  roles: Record<string, RoleStatus>
}

export function fetchProvidersStatus(): Promise<ProvidersStatus> {
  return apiGet<ProvidersStatus>('/providers/status')
}

/** True when the provider the generation role uses has a key. */
export function hasGenerationKey(status: ProvidersStatus): boolean {
  const provider = status.roles.generation?.provider
  return status.keys.some((key) => key.provider === provider && key.configured)
}
