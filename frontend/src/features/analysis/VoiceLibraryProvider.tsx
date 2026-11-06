import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import type { CreateVoiceResult, VoiceSummary } from '@/api/voices'
import { voiceLibraryKey } from './voice-assignments'
import { VoiceLibraryDialog } from './VoiceLibraryDialog'

interface VoiceLibraryContextValue {
  voicesById: Readonly<Record<string, VoiceSummary>>
  openManagement(): void
  mergeVoices(voices: VoiceSummary[]): void
  markDeleted(voice: VoiceSummary): void
  isDeleted(voiceId: string): boolean
  rememberCreatedVoice(voice: CreateVoiceResult): void
}

const VoiceLibraryContext = createContext<VoiceLibraryContextValue | undefined>(undefined)

export function VoiceLibraryProvider({ children }: { children: ReactNode }) {
  const [voicesById, setVoicesById] = useState<Record<string, VoiceSummary>>({})
  const deletedIds = useRef(new Set<string>())
  const [open, setOpen] = useState(false)
  const returnFocus = useRef<HTMLElement | null>(null)
  const openManagement = useCallback(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setOpen(true)
  }, [])
  const mergeVoices = useCallback((voices: VoiceSummary[]) => {
    setVoicesById((current) => {
      const next = { ...current }
      for (const voice of voices) {
        const key = voiceLibraryKey(voice.voice_id)
        if (deletedIds.current.has(key)) continue
        const previous = current[key]
        if (previous && !hasNewerVoiceCheck(voice, previous)) continue
        next[key] = { ...voice, voice_id: key }
      }
      return next
    })
  }, [])
  const markDeleted = useCallback((voice: VoiceSummary) => {
    const key = voiceLibraryKey(voice.voice_id)
    deletedIds.current.add(key)
    setVoicesById((current) => ({ ...current, [key]: { ...voice, voice_id: key, availability: 'missing', expiry_state: 'unknown' } }))
  }, [])
  const rememberCreatedVoice = useCallback((voice: CreateVoiceResult) => mergeVoices([{
    voice_id: voice.voice_id, display_name: voice.display_name ?? null, speaker_label: voice.speaker,
    voice_traits: voice.voice_traits, language_code: null, created_at: null, created_at_source: 'unknown',
    expire_time: null, availability: 'available', expiry_state: 'unknown', last_checked_at: null, usage_count: null,
  }]), [mergeVoices])
  const isDeleted = useCallback((voiceId: string) => deletedIds.current.has(voiceLibraryKey(voiceId)), [])
  const value = useMemo(() => ({ voicesById, openManagement, mergeVoices, markDeleted, isDeleted, rememberCreatedVoice }), [voicesById, openManagement, mergeVoices, markDeleted, isDeleted, rememberCreatedVoice])
  return <VoiceLibraryContext.Provider value={value}>{children}<VoiceLibraryDialog onCloseAutoFocus={(event) => { event.preventDefault(); returnFocus.current?.focus() }} open={open} onOpenChange={setOpen} onVoicesChange={mergeVoices} onVoiceDeleted={markDeleted} /></VoiceLibraryContext.Provider>
}

// oxlint-disable-next-line react/only-export-components
export function useVoiceLibrary(): VoiceLibraryContextValue {
  const value = useContext(VoiceLibraryContext)
  if (!value) throw new Error('VoiceLibraryProvider is required')
  return value
}

function hasNewerVoiceCheck(candidate: VoiceSummary, current: VoiceSummary): boolean {
  if (!candidate.last_checked_at) return false
  const candidateCheckedAt = Date.parse(candidate.last_checked_at)
  if (!Number.isFinite(candidateCheckedAt)) return false
  if (!current.last_checked_at) return true
  const currentCheckedAt = Date.parse(current.last_checked_at)
  return !Number.isFinite(currentCheckedAt) || candidateCheckedAt > currentCheckedAt
}
