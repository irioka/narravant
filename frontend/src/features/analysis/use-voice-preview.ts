import { useCallback, useEffect, useRef, useState } from 'react'
import { previewVoice, type VoiceSummary } from '@/api/voices'
import { defaultAudioContext, playPcm16 } from './audio-decoder'
import { voiceLibraryKey } from './voice-assignments'

export function voiceCanBePreviewed(voice: VoiceSummary): boolean {
  return voice.availability === 'available' && voice.expiry_state !== 'expired' && voice.expiry_state !== 'stale'
}

export function useVoicePreview({ documentId, isDraft, text, disabled, voicesById, isDeleted, createContext = defaultAudioContext }: {
  documentId: string
  isDraft: boolean
  text: string
  disabled: boolean
  voicesById: Readonly<Record<string, VoiceSummary>>
  isDeleted?: (voiceId: string) => boolean
  createContext?: () => AudioContext | null
}) {
  const generation = useRef(0)
  const flight = useRef<{ voiceId: string; controller: AbortController; context: AudioContext | null; ownsContext: boolean } | undefined>(undefined)
  const [loadingVoiceId, setLoadingVoiceId] = useState<string>()
  const [activeVoiceId, setActiveVoiceId] = useState<string>()
  const [error, setError] = useState<unknown>()
  const stop = useCallback(() => {
    generation.current += 1
    const current = flight.current
    flight.current = undefined
    current?.controller.abort()
    if (current?.ownsContext && current.context && current.context.state !== 'closed') {
      current.ownsContext = false
      void current.context.close().catch(() => undefined)
    }
    setLoadingVoiceId(undefined)
    setActiveVoiceId(undefined)
    setError(undefined)
  }, [])

  useEffect(() => stop, [documentId, isDraft, text, stop])
  useEffect(() => {
    const id = flight.current?.voiceId
    const voice = id ? voicesById[voiceLibraryKey(id)] : undefined
    if (disabled || (voice && !voiceCanBePreviewed(voice))) stop()
  }, [disabled, voicesById, stop])

  const play = async (voice: VoiceSummary) => {
    const shared = voicesById[voiceLibraryKey(voice.voice_id)]
    if (disabled || isDeleted?.(voice.voice_id) || !text.trim() || !voiceCanBePreviewed(shared ?? voice) || flight.current?.voiceId === voice.voice_id) return
    stop()
    const requestGeneration = ++generation.current
    const controller = new AbortController()
    const request = { voiceId: voice.voice_id, controller, context: null as AudioContext | null, ownsContext: true }
    flight.current = request
    setLoadingVoiceId(voice.voice_id)
    try {
      request.context = createContext()
      // Resume during the click gesture, before waiting for the network response.
      const resume = request.context?.state === 'suspended' ? request.context.resume() : Promise.resolve()
      const [pcm] = await Promise.all([previewVoice(documentId, { voice_id: voice.voice_id, text }, fetch, isDraft, controller.signal), resume])
      if (controller.signal.aborted || requestGeneration !== generation.current || isDeleted?.(voice.voice_id)) return
      setLoadingVoiceId(undefined)
      setActiveVoiceId(voice.voice_id)
      request.ownsContext = false
      await playPcm16(pcm, 24000, () => request.context, controller.signal)
    } catch (cause) {
      if (!controller.signal.aborted && requestGeneration === generation.current) setError(cause)
    } finally {
      if (request.ownsContext && request.context && request.context.state !== 'closed') {
        request.ownsContext = false
        await request.context.close().catch(() => undefined)
      }
      if (requestGeneration === generation.current) {
        flight.current = undefined
        setLoadingVoiceId(undefined)
        setActiveVoiceId(undefined)
      }
    }
  }
  return { text, disabled, loadingVoiceId, activeVoiceId, error, play, stop }
}
