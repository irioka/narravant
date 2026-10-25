import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { ApiClientError } from '@/api/client'
import { createVoice, deleteVoice, VOICE_LIMIT_EXCEEDED, type VoiceSummary } from '@/api/voices'
import { upsertVoiceAssignment, voiceLibraryKey, type VoiceAssignment } from './voice-assignments'
import { voiceCreationFailure, type VoiceCreationFailure } from './voice-creation-errors'

/** 編集中の割当と、このダイアログで生成した声の所有権を別々に保持する。 */
export function useEditorVoiceDraft({ assignments, documentId, documentTitle, isDraft, onCreationFailed, onLimitExceeded, onDeleted }: {
  assignments: VoiceAssignment[]
  documentId: string
  documentTitle: string
  isDraft: boolean
  onCreationFailed: (failure: VoiceCreationFailure) => void
  onLimitExceeded: () => void
  onDeleted: (voiceId: string) => void
}) {
  const { t } = useTranslation('analysis')
  const [pending, setPending] = useState<VoiceAssignment>()
  const pendingRef = useRef<VoiceAssignment | undefined>(undefined)
  const generated = useRef<VoiceAssignment | undefined>(undefined)
  const protectedIds = useRef(new Set(assignments.flatMap(({ voice_id }) => voice_id ? [voiceLibraryKey(voice_id)] : [])))
  const running = useRef<Promise<boolean> | undefined>(undefined)
  const closing = useRef(false)
  const [busy, setBusy] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')

  const updatePending = (value?: VoiceAssignment) => {
    pendingRef.current = value
    setPending(value)
  }

  const forgetDeleted = (voiceId: string) => {
    const key = voiceLibraryKey(voiceId)
    if (voiceLibraryKey(generated.current?.voice_id ?? '') === key) generated.current = undefined
    if (voiceLibraryKey(pendingRef.current?.voice_id ?? '') === key) updatePending(undefined)
  }

  const deleteGenerated = async () => {
    const voice = generated.current
    if (!voice?.voice_id) return
    // Assignした既存声や、開く前の割当を削除対象にしてはならない。
    if (protectedIds.current.has(voiceLibraryKey(voice.voice_id))) return
    await deleteVoice(voice.voice_id)
    forgetDeleted(voice.voice_id)
    onDeleted(voice.voice_id)
  }

  const execute = (action: () => Promise<void>): Promise<boolean> => {
    if (running.current || closing.current) return Promise.resolve(false)
    setBusy(true)
    setError('')
    const request = (async () => {
      try {
        await action()
        return true
      } catch {
        setError(t('voices.temporaryDeleteFailed'))
        return false
      } finally {
        running.current = undefined
        setBusy(false)
      }
    })()
    running.current = request
    return request
  }

  const generate = (speaker: string, traits: string) => execute(async () => {
    await deleteGenerated()
    setGenerating(true)
    try {
      const input = { speaker, voice_traits: traits, display_name: `${documentTitle} ; ${speaker}` }
      const result = isDraft
        ? await createVoice(documentId, input, undefined, true)
        : await createVoice(documentId, input)
      if (result.registry_sync_pending) toast.warning(t('voices.registrySyncPending'))
      if (result.voice_id && !protectedIds.current.has(voiceLibraryKey(result.voice_id))) generated.current = result
      updatePending(result)
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.code === VOICE_LIMIT_EXCEEDED) onLimitExceeded()
      else onCreationFailed(voiceCreationFailure(cause, speaker))
    } finally {
      setGenerating(false)
    }
  })

  const assign = (voice: VoiceSummary, speaker: string, traits: string) => execute(async () => {
    if (voiceLibraryKey(generated.current?.voice_id ?? '') !== voiceLibraryKey(voice.voice_id)) await deleteGenerated()
    updatePending({ speaker, voice_id: voice.voice_id, voice_traits: voice.voice_traits ?? traits })
  })

  const cancel = async (onClose: () => void) => {
    if (closing.current) return
    closing.current = true
    setCancelling(true)
    setError('')
    // 作成応答が返る前に閉じず、返った声IDを削除してから編集を破棄する。
    await running.current
    try {
      await deleteGenerated()
      updatePending(undefined)
      onClose()
    } catch {
      setError(t('voices.temporaryDeleteFailed'))
    } finally {
      closing.current = false
      setCancelling(false)
    }
  }

  const save = (onSave: (assignment?: VoiceAssignment) => boolean | void) => {
    if (running.current || closing.current) return
    if (onSave(pendingRef.current) !== false) generated.current = undefined
  }

  return {
    assignments: pending ? upsertVoiceAssignment(assignments, pending) : assignments,
    generate, assign, cancel, save, forgetDeleted, error, generating, busy: busy || cancelling, cancelling,
  }
}
