import { AlertTriangle, ChevronDown, LoaderCircle, RefreshCw, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { deleteVoice, getVoice, getVoiceUsages, listVoices, type VoiceSummary, type VoiceUsageList } from '@/api/voices'
import { localizeUiError, toUiError } from '@/i18n/errors'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { voiceLibraryKey } from './voice-assignments'

export interface VoiceLibraryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelect?: (voice: VoiceSummary) => boolean | void | Promise<boolean | void>
  onVoicesChange?: (voices: VoiceSummary[]) => void
  onVoiceDeleted?: (voice: VoiceSummary) => void
  selectedVoiceId?: string
  assignedVoiceIds?: string[]
}

function errorMessage(error: unknown, t: TFunction<'errors'>): string {
  return localizeUiError(t, toUiError(error, 'HTTP_ERROR')).message
}

const voiceDateFormatter = new Intl.DateTimeFormat('ja-JP', {
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

function formatVoiceDateTime(value: string): string | null {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const parts = Object.fromEntries(voiceDateFormatter.formatToParts(date).map(({ type, value: partValue }) => [type, partValue]))
  const { year, month, day, hour, minute, second } = parts
  if (!year || !month || !day || !hour || !minute || !second) return null
  return `${year}/${month}/${day} ${hour}:${minute}:${second}`
}

function isUsable(voice: VoiceSummary): boolean {
  return voice.availability === 'available'
    && voice.expiry_state !== 'expired'
    && voice.expiry_state !== 'stale'
}

export function VoiceLibraryDialog({ open, onOpenChange, onSelect, onVoicesChange, onVoiceDeleted, selectedVoiceId, assignedVoiceIds = [] }: VoiceLibraryDialogProps) {
  const { t } = useTranslation(['analysis', 'common', 'errors'])
  const { t: tErrors } = useTranslation('errors')
  const [voices, setVoices] = useState<VoiceSummary[]>([])
  const [usagesByVoice, setUsagesByVoice] = useState<Record<string, VoiceUsageList>>({})
  const [loading, setLoading] = useState(false)
  const [busyVoiceId, setBusyVoiceId] = useState<string>()
  const [error, setError] = useState('')
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [deleteTargets, setDeleteTargets] = useState<{ voice: VoiceSummary; usages: VoiceUsageList }[]>([])
  const [deleting, setDeleting] = useState(false)
  const deleteInFlight = useRef(false)
  const refreshGeneration = useRef(0)
  const translationRef = useRef(t)
  const errorTranslationRef = useRef(tErrors)
  const voicesChangeRef = useRef(onVoicesChange)

  useEffect(() => {
    translationRef.current = t
    errorTranslationRef.current = tErrors
    voicesChangeRef.current = onVoicesChange
  }, [onVoicesChange, t, tErrors])

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current
    setLoading(true)
    setError('')
    try {
      const result = await listVoices()
      if (generation !== refreshGeneration.current) return
      setVoices(result.voices)
      setCheckedIds((current) => new Set([...current].filter((id) => result.voices.some((voice) => voice.voice_id === id))))
      voicesChangeRef.current?.(result.voices)
      if (!result.complete) setError(translationRef.current('analysis:voices.libraryIncomplete'))
    } catch (cause) {
      if (generation === refreshGeneration.current) setError(errorMessage(cause, errorTranslationRef.current))
    } finally {
      if (generation === refreshGeneration.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Opening the dialog starts a network refresh; its loading state belongs to that request.
    // oxlint-disable-next-line react/set-state-in-effect
    if (open) void refresh()
  }, [open, refresh])

  const updateVoice = (updated: VoiceSummary) => {
    refreshGeneration.current += 1
    setLoading(false)
    const next = voices.map((voice) => voice.voice_id === updated.voice_id ? updated : voice)
    setVoices(next)
    onVoicesChange?.(next)
  }

  const selectVoice = async (voice: VoiceSummary) => {
    if (!onSelect || busyVoiceId) return
    setBusyVoiceId(voice.voice_id)
    setError('')
    try {
      const current = await getVoice(voice.voice_id)
      updateVoice(current)
      if (!isUsable(current)) {
        setError(t('analysis:voices.voiceCannotBeAssigned'))
        return
      }
      if (await onSelect(current) === false) {
        setError(t('analysis:voices.temporaryDeleteFailed'))
        return
      }
      onOpenChange(false)
    } catch (cause) {
      setError(errorMessage(cause, tErrors))
    } finally {
      setBusyVoiceId(undefined)
    }
  }

  const toggleUsages = async (voice: VoiceSummary) => {
    if (busyVoiceId) return
    if (usagesByVoice[voice.voice_id]) {
      setUsagesByVoice((current) => {
        const next = { ...current }
        delete next[voice.voice_id]
        return next
      })
      return
    }
    setBusyVoiceId(voice.voice_id)
    setError('')
    try {
      const usages = await getVoiceUsages(voice.voice_id)
      setUsagesByVoice((current) => ({ ...current, [voice.voice_id]: usages }))
    } catch (cause) {
      setError(errorMessage(cause, tErrors))
    } finally {
      setBusyVoiceId(undefined)
    }
  }

  const prepareDelete = async () => {
    if (busyVoiceId || deleting || deleteInFlight.current) return
    const targets = voices.filter((voice) => checkedIds.has(voice.voice_id))
    if (!targets.length) return
    setBusyVoiceId(targets[0].voice_id)
    setError('')
    try {
      const checked = []
      // 選択した全声の利用先を確認してから、一括の削除確認を出す。
      for (const voice of targets) {
        const usages = await getVoiceUsages(voice.voice_id)
        setUsagesByVoice((current) => ({ ...current, [voice.voice_id]: usages }))
        if (!usages.complete) {
          setError(t('analysis:voices.libraryIncomplete'))
          return
        }
        checked.push({ voice, usages })
      }
      setDeleteTargets(checked)
    } catch (cause) {
      setError(errorMessage(cause, tErrors))
    } finally {
      setBusyVoiceId(undefined)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTargets.length || deleteInFlight.current || deleteTargets.some(({ usages }) => !usages.complete)) return
    deleteInFlight.current = true
    setDeleting(true)
    let deletedCount = 0
    let remaining = voices
    try {
      for (const { voice } of deleteTargets) {
        // timeoutでは実際の削除結果が不明なため、DELETEを自動再試行しない。
        await deleteVoice(voice.voice_id)
        deletedCount += 1
        // 削除前に始まった一覧取得の応答で声を復活させない。
        refreshGeneration.current += 1
        setLoading(false)
        remaining = remaining.filter((item) => item.voice_id !== voice.voice_id)
        setVoices(remaining)
        onVoicesChange?.(remaining)
        onVoiceDeleted?.(voice)
        setCheckedIds((current) => {
          const next = new Set(current)
          next.delete(voice.voice_id)
          return next
        })
        setUsagesByVoice((current) => {
          const next = { ...current }
          delete next[voice.voice_id]
          return next
        })
      }
      toast.success(t('analysis:voices.voicesDeleted', { count: deletedCount }))
    } catch (cause) {
      const message = t('analysis:voices.bulkDeleteFailed', { count: deletedCount, message: errorMessage(cause, tErrors) })
      setError(message)
      toast.error(message)
    } finally {
      setDeleteTargets([])
      deleteInFlight.current = false
      setDeleting(false)
    }
  }

  const blocked = Boolean(busyVoiceId) || deleting
  const changeOpen = (nextOpen: boolean) => {
    if (blocked) return
    if (!nextOpen) {
      refreshGeneration.current += 1
      setCheckedIds(new Set())
      setDeleteTargets([])
    }
    onOpenChange(nextOpen)
  }
  const allChecked = voices.length > 0 && voices.every((voice) => checkedIds.has(voice.voice_id))
  const toggleChecked = (voiceId: string) => {
    setCheckedIds((current) => {
      const next = new Set(current)
      if (next.has(voiceId)) next.delete(voiceId)
      else next.add(voiceId)
      return next
    })
  }

  const assignedVoiceIdSet = new Set(assignedVoiceIds.map(voiceLibraryKey))
  const sortedVoices = [...voices].sort((left, right) => {
    const leftAssigned = assignedVoiceIdSet.has(voiceLibraryKey(left.voice_id))
    const rightAssigned = assignedVoiceIdSet.has(voiceLibraryKey(right.voice_id))
    if (leftAssigned !== rightAssigned) return leftAssigned ? -1 : 1

    const leftCreatedAt = left.created_at ? Date.parse(left.created_at) : Number.NaN
    const rightCreatedAt = right.created_at ? Date.parse(right.created_at) : Number.NaN
    const leftHasCreatedAt = Number.isFinite(leftCreatedAt)
    const rightHasCreatedAt = Number.isFinite(rightCreatedAt)
    if (leftHasCreatedAt !== rightHasCreatedAt) return leftHasCreatedAt ? -1 : 1
    if (leftHasCreatedAt && rightHasCreatedAt && leftCreatedAt !== rightCreatedAt) return rightCreatedAt - leftCreatedAt

    if (!leftHasCreatedAt && !rightHasCreatedAt) {
      const leftExpiresAt = left.expire_time ? Date.parse(left.expire_time) : Number.NaN
      const rightExpiresAt = right.expire_time ? Date.parse(right.expire_time) : Number.NaN
      const leftHasExpiry = Number.isFinite(leftExpiresAt)
      const rightHasExpiry = Number.isFinite(rightExpiresAt)
      if (leftHasExpiry !== rightHasExpiry) return leftHasExpiry ? -1 : 1
      if (leftHasExpiry && rightHasExpiry && leftExpiresAt !== rightExpiresAt) return rightExpiresAt - leftExpiresAt
    }
    return 0
  })

  return <>
    <Dialog onOpenChange={changeOpen} open={open}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('analysis:voices.libraryTitle')}</DialogTitle>
          <DialogDescription>{t('analysis:voices.libraryDescription')}</DialogDescription>
        </DialogHeader>
        {error && <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-sm text-destructive" role="alert">{error}</p>}
        {loading && voices.length === 0 && <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><LoaderCircle className="size-4 animate-spin" />{t('analysis:voices.libraryLoading')}</p>}
        {!loading && voices.length === 0 && !error && <p className="text-sm text-muted-foreground">{t('analysis:voices.libraryEmpty')}</p>}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input aria-checked={allChecked ? true : checkedIds.size > 0 ? 'mixed' : false} checked={allChecked} className="size-4" disabled={blocked || voices.length === 0} onChange={() => setCheckedIds(allChecked ? new Set() : new Set(voices.map((voice) => voice.voice_id)))} ref={(element) => { if (element) element.indeterminate = !allChecked && checkedIds.size > 0 }} type="checkbox" />
            {t('analysis:voices.selectAll')}
          </label>
          <Button disabled={blocked || checkedIds.size === 0} onClick={() => { void prepareDelete() }} size="sm" variant="destructive">
            {busyVoiceId ? <LoaderCircle className="animate-spin" /> : <Trash2 />}{t('analysis:voices.deleteSelected', { count: checkedIds.size })}
          </Button>
        </div>
        <div aria-label={t('analysis:voices.libraryTitle')} className="space-y-3" role="list">
          {sortedVoices.map((voice) => {
            const expandedUsages = usagesByVoice[voice.voice_id]
            const availabilityKey = voice.availability === 'available' ? 'available' : voice.availability
            const expiryKey = voice.expiry_state
            const createdAt = voice.created_at ? formatVoiceDateTime(voice.created_at) : null
            const expiresAt = voice.expire_time ? formatVoiceDateTime(voice.expire_time) : null
            return <article className="space-y-2 rounded-md border p-3" key={voice.voice_id} role="listitem">
              <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3">
                <div className="min-w-0 space-y-1">
                  <div className="flex items-start gap-2">
                    <input aria-label={t('analysis:voices.selectVoice', { voice: voice.display_name || voice.speaker_label || voice.voice_id })} checked={checkedIds.has(voice.voice_id)} className="mt-1 size-4 shrink-0" disabled={blocked} onChange={() => toggleChecked(voice.voice_id)} type="checkbox" />
                    <h3 className="break-words font-medium">{voice.display_name || voice.speaker_label || voice.voice_id}</h3>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="min-w-0 break-all font-mono text-xs text-muted-foreground">{voice.voice_id}</p>
                    {selectedVoiceId && voiceLibraryKey(selectedVoiceId) === voice.voice_id && <Badge variant="secondary">{t('analysis:voices.currentlyAssigned')}</Badge>}
                    <Badge variant={voice.availability === 'available' ? 'outline' : 'destructive'}>{t(`analysis:voices.availability.${availabilityKey}`)}</Badge>
                    <Badge variant={voice.expiry_state === 'expired' ? 'destructive' : 'outline'}>{t(`analysis:voices.expiry.${expiryKey}`)}</Badge>
                  </div>
                  {voice.voice_traits && <p className="whitespace-pre-wrap text-sm text-muted-foreground">{voice.voice_traits}</p>}
                  <p className="text-xs text-muted-foreground">{createdAt && <>
                    <span>{t('analysis:voices.createdAt', { date: createdAt })}</span>
                    <span>{t('analysis:voices.dateSeparator')}</span>
                  </>}{expiresAt
                    ? t('analysis:voices.expiresAt', { date: expiresAt })
                    : t('analysis:voices.expiryUnknown')}</p>
                  <Button aria-controls={`voice-usages-${voice.voice_id}`} aria-expanded={Boolean(expandedUsages)} className="h-auto justify-start gap-1 p-0 text-xs text-muted-foreground" disabled={blocked} onClick={() => { void toggleUsages(voice) }} size="sm" variant="ghost">
                    {voice.usage_count === null
                      ? t('analysis:voices.usageCountUnknown')
                      : t('analysis:voices.usageCount', { count: voice.usage_count })}
                    {busyVoiceId === voice.voice_id ? <LoaderCircle className="size-3 animate-spin" /> : <ChevronDown className={`size-3 transition-transform ${expandedUsages ? 'rotate-180' : ''}`} />}
                  </Button>
                </div>
                <div className="flex shrink-0 items-start justify-end gap-1">
                  {onSelect && <Button disabled={blocked || !isUsable(voice)} onClick={() => { void selectVoice(voice) }} size="sm" variant="default">
                    {busyVoiceId === voice.voice_id ? <LoaderCircle className="animate-spin" /> : null}{t('analysis:voices.useVoice')}
                  </Button>}
                </div>
              </div>
              <div className="rounded bg-muted/40 p-2 text-sm" hidden={!expandedUsages} id={`voice-usages-${voice.voice_id}`}>
                {expandedUsages && <>
                  {!expandedUsages.complete && <p className="mb-2 flex items-center gap-1 text-destructive"><AlertTriangle className="size-4" />{t('analysis:voices.libraryIncomplete')}</p>}
                  {expandedUsages.usages.length === 0
                    ? <p className="text-muted-foreground">{t('analysis:voices.noUsages')}</p>
                    : <ul className="space-y-1">{expandedUsages.usages.map((usage) => <li key={`${usage.document_id}-${usage.speaker}`}>{usage.title} ; {usage.speaker}</li>)}</ul>}
                </>}
              </div>
            </article>
          })}
        </div>
        <DialogFooter>
          <Button disabled={loading || blocked || deleteTargets.length > 0} onClick={() => { void refresh() }} variant="outline">{loading ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{t('analysis:voices.refreshLibrary')}</Button>
          <Button disabled={blocked} onClick={() => changeOpen(false)} variant="outline">{t('common:actions.close')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog onOpenChange={(nextOpen) => { if (!nextOpen && !deleting) setDeleteTargets([]) }} open={deleteTargets.length > 0}>
      <AlertDialogContent className="max-h-[85vh] overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle>{t('analysis:voices.deleteSelectedTitle', { count: deleteTargets.length })}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('analysis:voices.deleteSelectedDescription', { count: deleteTargets.length })}
            {deleteTargets.map(({ voice, usages }) => <span className="mt-2 block" key={voice.voice_id}>
              <span className="block font-medium">{voice.display_name || voice.speaker_label || voice.voice_id}</span>
              {usages.usages.length === 0
                ? <span className="block">{t('analysis:voices.noUsages')}</span>
                : usages.usages.map((usage) => <span className="block" key={`${usage.document_id}-${usage.speaker}`}>{usage.title} ; {usage.speaker}</span>)}
            </span>)}
            <span className="mt-2 block">{t('analysis:voices.deleteImpact')}</span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t('common:actions.cancel')}</AlertDialogCancel>
          <AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={deleting || !deleteTargets.length || deleteTargets.some(({ usages }) => !usages.complete)} onClick={(event) => { event.preventDefault(); void confirmDelete() }}>
            {deleting ? <LoaderCircle className="animate-spin" /> : <Trash2 />}{deleting ? t('analysis:voices.deletingVoice') : t('common:actions.delete')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </>
}
