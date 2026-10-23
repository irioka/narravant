import { describe, expect, it } from 'vitest'
import { ApiClientError } from '@/api/client'
import { VOICE_LIMIT_EXCEEDED, VOICE_PROVIDER_UNAVAILABLE } from '@/api/voices'
import { voiceCreationFailure } from './voice-creation-errors'

describe('voiceCreationFailure', () => {
  it('providerの自由文をUI理由にしない', () => {
    const error = new ApiClientError(502, 'VALIDATION_ERROR', 'private sentinel', {
      operation: 'voice_design', reason: 'invalid_argument',
    })
    expect(voiceCreationFailure(error, '合成人物')).toEqual({ speaker: '合成人物', reason: 'invalid_argument' })
  })
  it.each(['invalid_argument', 'credentials', 'permission_denied', 'quota', 'provider_unavailable', 'transport_unknown', 'configuration', 'invalid_response', 'unknown'])('固定理由 %s を保持する', (reason) => {
    expect(voiceCreationFailure(new ApiClientError(502, 'VALIDATION_ERROR', 'private', { operation: 'voice_design', reason }), 'A').reason).toBe(reason)
  })
  it.each([undefined, {}, { reason: 'invalid_argument' }, { operation: 'other', reason: 'credentials' }, { operation: 'voice_design', reason: 'private' }, { operation: 'voice_design', reason: ['credentials'] }])('不正なcontextはunknown', (context) => {
    expect(voiceCreationFailure(new ApiClientError(502, 'VALIDATION_ERROR', 'private', context), 'A').reason).toBe('unknown')
  })
  it.each([[409, VOICE_LIMIT_EXCEEDED, 'quota'], [503, VOICE_PROVIDER_UNAVAILABLE, 'provider_unavailable']] as const)('既存wrapper判定を保持する', (status, code, reason) => {
    expect(voiceCreationFailure(new ApiClientError(status, code, 'private'), 'A').reason).toBe(reason)
  })
})
