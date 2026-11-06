import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getPlaybackSettings, PAUSE_FIELDS, type PlaybackPauseOverrides, type PlaybackSettings } from '@/api/playback'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

interface Props {
  open: boolean
  overrides?: PlaybackPauseOverrides
  onOpenChange(open: boolean): void
  onConfirm(overrides?: PlaybackPauseOverrides): void
}

export function PlaybackSettingsDialog({ open, overrides, onOpenChange, onConfirm }: Props) {
  const { t } = useTranslation(['analysis', 'common'])
  const [defaults, setDefaults] = useState<PlaybackSettings>()
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const attempted = useRef(false)
  const generation = useRef(0)
  const reload = useCallback(async () => {
    const current = ++generation.current
    attempted.current = true
    setLoading(true)
    setFailed(false)
    try {
      const settings = await getPlaybackSettings()
      if (current === generation.current) setDefaults(settings)
    } catch {
      if (current === generation.current) setFailed(true)
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }, [])
  useEffect(() => { if (open && !attempted.current) void reload() }, [open, reload])
  useEffect(() => () => { generation.current += 1 }, [])
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent>
      <DialogHeader><DialogTitle>{t('analysis:playbackSettings.title')}</DialogTitle><DialogDescription>{t('analysis:playbackSettings.nextPlay')}</DialogDescription></DialogHeader>
      {loading && <p role="status">{t('analysis:playbackSettings.loading')}</p>}
      {failed && <p role="alert">{t('analysis:playbackSettings.loadFailed')}</p>}
      {failed && <Button variant="outline" onClick={() => { void reload() }} disabled={loading}>{t('analysis:playbackSettings.reload')}</Button>}
      {defaults && !loading && !failed
        ? <PlaybackSettingsForm key={JSON.stringify(defaults)} defaults={defaults} overrides={overrides} onConfirm={onConfirm} onCancel={() => onOpenChange(false)} />
        : <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{t('common:actions.cancel')}</Button><Button disabled>{t('common:actions.ok')}</Button></DialogFooter>}
    </DialogContent>
  </Dialog>
}

function PlaybackSettingsForm({ defaults, overrides, onConfirm, onCancel }: {
  defaults: PlaybackSettings
  overrides?: PlaybackPauseOverrides
  onConfirm(overrides?: PlaybackPauseOverrides): void
  onCancel(): void
}) {
  const { t } = useTranslation(['analysis', 'common'])
  const toFields = (values: PlaybackPauseOverrides) => Object.fromEntries(PAUSE_FIELDS.map((name) => [name, (values[name] / 1000).toFixed(1)])) as Record<typeof PAUSE_FIELDS[number], string>
  const [raw, setRaw] = useState(() => toFields(overrides ?? defaults))
  const [edited, setEdited] = useState(false)
  const [reset, setReset] = useState(false)
  const values = {} as PlaybackPauseOverrides
  let valid = true
  for (const name of PAUSE_FIELDS) {
    const value = Number(raw[name]), ms = Math.round(value * 1000)
    if (!raw[name].trim() || !Number.isFinite(value) || value < defaults.min_ms / 1000 || value > defaults.max_ms / 1000 || Math.abs(value * 10 - Math.round(value * 10)) > 1e-8 || !Number.isInteger(ms) || ms < defaults.min_ms || ms > defaults.max_ms || ms % defaults.step_ms !== 0) valid = false
    values[name] = ms
  }
  if (!edited && !reset && !overrides && PAUSE_FIELDS.some((name) => defaults[name] < defaults.min_ms || defaults[name] > defaults.max_ms)) valid = false
  const roundedDefaults = PAUSE_FIELDS.some((name) => defaults[name] % defaults.step_ms !== 0)
  const confirm = () => { if (reset || valid) onConfirm(reset || (!edited && !overrides && !roundedDefaults) ? undefined : values) }
  return <>
    {PAUSE_FIELDS.map((name) => <label key={name} className="grid gap-1 text-sm">{t(`analysis:playbackSettings.${name}`)} (sec)
      <Input type="number" min={defaults.min_ms / 1000} max={defaults.max_ms / 1000} step={defaults.step_ms / 1000} value={raw[name]} onChange={(event) => { setRaw((current) => ({ ...current, [name]: event.target.value })); setEdited(true); setReset(false) }} />
    </label>)}
    {!valid && !reset && <p role="alert" className="text-sm text-destructive">{t('analysis:playbackSettings.invalid')}</p>}
    <Button variant="outline" onClick={() => { setRaw(toFields(defaults)); setReset(true); setEdited(false) }}>{t('analysis:playbackSettings.reset')}</Button>
    <DialogFooter><Button variant="outline" onClick={onCancel}>{t('common:actions.cancel')}</Button><Button disabled={!valid && !reset} onClick={confirm}>{t('common:actions.ok')}</Button></DialogFooter>
  </>
}
