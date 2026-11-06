import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ReactNode } from 'react'
import type { VoiceSummary } from '@/api/voices'
import { VoiceLibraryProvider, useVoiceLibrary } from './VoiceLibraryProvider'

afterEach(cleanup)
const voice: VoiceSummary = {
  voice_id: 'voice_synthetic', display_name: 'Synthetic voice', speaker_label: null,
  voice_traits: null, language_code: null, created_at: null, created_at_source: 'unknown',
  expire_time: null, availability: 'available', expiry_state: 'unknown',
  last_checked_at: '2026-11-06T00:00:00Z', usage_count: null,
}
const wrapper = ({ children }: { children: ReactNode }) => <VoiceLibraryProvider>{children}</VoiceLibraryProvider>

describe('VoiceLibraryProvider', () => {
  it('keeps a canonical deletion tombstone against delayed list/get responses', async () => {
    const { result } = renderHook(useVoiceLibrary, { wrapper })
    let resolveList!: (voices: VoiceSummary[]) => void
    const list = new Promise<VoiceSummary[]>((resolve) => { resolveList = resolve })
    const delayed = list.then((voices) => result.current.mergeVoices(voices))
    act(() => result.current.mergeVoices([voice]))
    act(() => result.current.markDeleted({ ...voice, voice_id: 'voices/voice_synthetic' }))
    await act(async () => { resolveList([{ ...voice, last_checked_at: '2026-11-07T00:00:00Z' }]); await delayed })
    expect(result.current.voicesById.voice_synthetic.availability).toBe('missing')
  })

  it('merges incomplete snapshots without deleting absent voices and rejects older checks', () => {
    const { result } = renderHook(useVoiceLibrary, { wrapper })
    act(() => result.current.mergeVoices([voice]))
    act(() => result.current.mergeVoices([]))
    act(() => result.current.mergeVoices([{ ...voice, availability: 'unknown', last_checked_at: null }]))
    expect(result.current.voicesById.voice_synthetic).toEqual(voice)
  })
})
