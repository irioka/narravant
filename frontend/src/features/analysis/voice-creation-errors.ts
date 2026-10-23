import { ApiClientError } from '@/api/client'
import { VOICE_LIMIT_EXCEEDED, VOICE_PROVIDER_UNAVAILABLE } from '@/api/voices'

const failureReasons = [
  'invalid_argument', 'credentials', 'permission_denied', 'quota', 'provider_unavailable',
  'transport_unknown', 'configuration', 'invalid_response', 'unknown',
] as const

export type VoiceDesignFailureReason = typeof failureReasons[number]
export type VoiceCreationFailure = { speaker: string; reason: VoiceDesignFailureReason }

export function voiceCreationFailure(error: unknown, speaker: string): VoiceCreationFailure {
  if (error instanceof ApiClientError) {
    // contextのない既存409/503はwrapperが分類したcodeを使う。
    if (error.status === 409 && error.code === VOICE_LIMIT_EXCEEDED) return { speaker, reason: 'quota' }
    if (error.status === 503 && error.code === VOICE_PROVIDER_UNAVAILABLE) return { speaker, reason: 'provider_unavailable' }
    const reason = error.context?.reason
    if (error.context?.operation === 'voice_design' && typeof reason === 'string'
      && failureReasons.some((candidate) => candidate === reason)) {
      return { speaker, reason: reason as VoiceDesignFailureReason }
    }
  }
  return { speaker, reason: 'unknown' }
}
