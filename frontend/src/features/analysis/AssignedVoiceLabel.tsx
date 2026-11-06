import { LoaderCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { useVoiceLibrary } from './VoiceLibraryProvider'
import { voiceLibraryKey } from './voice-assignments'

export function AssignedVoiceLabel({ voiceId }: { voiceId?: string }) {
  const { t } = useTranslation('analysis')
  const { voicesById } = useVoiceLibrary()
  const voice = voiceId ? voicesById[voiceLibraryKey(voiceId)] : undefined
  const unavailable = voice && (voice.availability !== 'available' || voice.expiry_state === 'expired' || voice.expiry_state === 'stale')
  const name = voice?.display_name || voiceId || ''
  const loading = Boolean(voiceId && !voice)
  const label = loading ? t('voices.nameLoading') : voiceId ? t('voices.assignedName', { name }) : t('voices.unassigned')
  return <Badge aria-busy={loading || undefined} className="min-w-0 max-w-48 gap-1 font-normal" role={loading ? 'status' : undefined} title={voiceId ? `${label} (${voiceId})` : label} variant={unavailable ? 'destructive' : 'outline'}>
    {loading && <LoaderCircle aria-hidden className="size-3 shrink-0 animate-spin" />}
    <span className="truncate">{label}{unavailable ? ` · ${t('voices.assignedUnavailable')}` : ''}</span>
  </Badge>
}
