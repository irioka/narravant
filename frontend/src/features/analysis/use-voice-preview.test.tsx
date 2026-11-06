import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { previewVoice, type VoiceSummary } from '@/api/voices'
import { VoiceLibraryProvider, useVoiceLibrary } from './VoiceLibraryProvider'
import { useVoicePreview } from './use-voice-preview'

vi.mock('@/api/voices', () => ({ previewVoice: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const voice = { voice_id: 'voice_a', availability: 'available', expiry_state: 'unknown' } as VoiceSummary
const pcm = new Uint8Array(2)
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
function audio() {
  const source = { connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn(), onended: null as (() => void) | null }
  const context = { state: 'running', close: vi.fn().mockImplementation(async () => { context.state = 'closed' }), resume: vi.fn().mockResolvedValue(undefined), destination: {}, createBuffer: () => ({ getChannelData: () => new Float32Array(1) }), createBufferSource: () => source }
  return { source, context, createContext: () => context as unknown as AudioContext }
}
const input = { documentId: 'synthetic', isDraft: true, text: 'Latest text', disabled: false, voicesById: {} }

describe('useVoicePreview', () => {
  it('invalidates a delayed response on stop and closes the precreated context', async () => {
    const response = deferred<Uint8Array>()
    vi.mocked(previewVoice).mockReturnValueOnce(response.promise)
    const fake = audio()
    const { result } = renderHook(() => useVoicePreview({ ...input, createContext: fake.createContext }))
    let playing!: Promise<void>
    act(() => { playing = result.current.play(voice) })
    expect(result.current.loadingVoiceId).toBe('voice_a')
    act(() => result.current.stop())
    await act(async () => { response.resolve(pcm); await playing })
    expect(fake.source.start).not.toHaveBeenCalled()
    expect(fake.context.close).toHaveBeenCalledTimes(1)
    expect(result.current.loadingVoiceId).toBeUndefined()
    expect(result.current.error).toBeUndefined()
    expect(vi.mocked(previewVoice).mock.calls[0][4]?.aborted).toBe(true)
  })

  it('switches candidates, suppresses duplicates, and releases the playing source', async () => {
    vi.mocked(previewVoice).mockResolvedValue(pcm)
    const first = audio(), second = audio()
    const createContext = vi.fn().mockReturnValueOnce(first.context).mockReturnValueOnce(second.context)
    const { result } = renderHook(() => useVoicePreview({ ...input, createContext }))
    let a!: Promise<void>, b!: Promise<void>
    act(() => { a = result.current.play(voice) })
    await waitFor(() => expect(first.source.start).toHaveBeenCalledOnce())
    await act(() => result.current.play(voice))
    expect(previewVoice).toHaveBeenCalledTimes(1)
    act(() => { b = result.current.play({ ...voice, voice_id: 'voice_b' }) })
    await waitFor(() => expect(second.source.start).toHaveBeenCalledOnce())
    expect(first.source.stop).toHaveBeenCalled()
    expect(first.source.disconnect).toHaveBeenCalled()
    expect(first.context.close).toHaveBeenCalled()
    act(() => result.current.stop())
    await act(async () => { await Promise.all([a, b]) })
    expect(second.source.disconnect).toHaveBeenCalled()
  })

  it('rejects delayed resume after text changes and unmount', async () => {
    vi.mocked(previewVoice).mockResolvedValue(pcm)
    const resume = deferred<void>(), fake = audio()
    fake.context.state = 'suspended'
    fake.context.resume.mockReturnValue(resume.promise)
    const { result, rerender, unmount } = renderHook(({ text }) => useVoicePreview({ ...input, text, createContext: fake.createContext }), { initialProps: { text: input.text } })
    let playing!: Promise<void>
    act(() => { playing = result.current.play(voice) })
    rerender({ text: 'Changed text' })
    unmount()
    await act(async () => { resume.resolve(); await playing })
    expect(fake.source.start).not.toHaveBeenCalled()
    expect(fake.context.close).toHaveBeenCalled()
  })

  it('stops on shared deletion, ignores missing entries in incomplete snapshots', async () => {
    const response = deferred<Uint8Array>(), fake = audio()
    vi.mocked(previewVoice).mockReturnValue(response.promise)
    const { result, rerender } = renderHook(({ voicesById }) => useVoicePreview({ ...input, voicesById, createContext: fake.createContext }), { initialProps: { voicesById: {} as Record<string, VoiceSummary> } })
    let playing!: Promise<void>
    act(() => { playing = result.current.play(voice) })
    rerender({ voicesById: {} })
    expect(result.current.loadingVoiceId).toBe('voice_a')
    rerender({ voicesById: { voice_a: { ...voice, availability: 'missing' } } })
    await act(async () => { response.resolve(pcm); await playing })
    expect(fake.source.start).not.toHaveBeenCalled()
    expect(result.current.loadingVoiceId).toBeUndefined()
  })

  it('keeps a failed preview retryable and uses the current text without creating a voice', async () => {
    const fake = audio()
    vi.mocked(previewVoice).mockRejectedValueOnce(new Error('Synthetic failure')).mockResolvedValue(pcm)
    const { result } = renderHook(() => useVoicePreview({ ...input, createContext: fake.createContext }))
    await act(() => result.current.play(voice))
    expect(result.current.error).toBeInstanceOf(Error)
    expect(result.current.loadingVoiceId).toBeUndefined()
    expect(previewVoice).toHaveBeenCalledWith('synthetic', { voice_id: 'voice_a', text: 'Latest text' }, fetch, true, expect.any(AbortSignal))
  })
})


it('does not revive audio when deletion, an old list, and preview response settle in the same batch', async () => {
  const response = deferred<Uint8Array>(), fake = audio()
  vi.mocked(previewVoice).mockReturnValue(response.promise)
  const { result } = renderHook(() => {
    const library = useVoiceLibrary()
    const preview = useVoicePreview({ ...input, createContext: fake.createContext, voicesById: library.voicesById, isDeleted: library.isDeleted })
    return { library, preview }
  }, { wrapper: VoiceLibraryProvider })
  let playing!: Promise<void>
  act(() => { playing = result.current.preview.play(voice) })
  await act(async () => {
    result.current.library.markDeleted(voice)
    result.current.library.mergeVoices([{ ...voice, last_checked_at: '2099-01-01T00:00:00Z' }])
    response.resolve(pcm)
    await playing
  })
  expect(result.current.library.voicesById.voice_a.availability).toBe('missing')
  expect(fake.source.start).not.toHaveBeenCalled()
  expect(fake.context.close).toHaveBeenCalled()
  expect(result.current.preview.loadingVoiceId).toBeUndefined()
})
