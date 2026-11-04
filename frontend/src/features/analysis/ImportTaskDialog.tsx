import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Progress } from '@/components/ui/progress'
import { localizeUiError } from '@/i18n/errors'
import { useImportSession } from './ImportSessionProvider'
import { importTaskRuntimeError } from './import-error'
export function ImportTaskDialog({ onOpenResult, blockedReason }: { onOpenResult: (operationId: string) => void; blockedReason?: string }) {
  const { t } = useTranslation(['analysis', 'common', 'importExport'])
  const { t: errorT } = useTranslation('errors')
  const { session, actions } = useImportSession()
  const [now, setNow] = useState(Date.now)
  const [discardOpen, setDiscardOpen] = useState(false)
  const active = ['starting', 'running', 'cancelling', 'unknown'].includes(session.phase)
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  const percentage = session.phase === 'completed' ? 100 : session.progress?.percentage ?? 0
  const terminal = ['completed', 'cancelled', 'failed'].includes(session.phase)
  const progressPhase = session.progress?.phase ?? 'queued'
  const phaseLabel = session.phase === 'running'
    ? t(`importExport:phase.${progressPhase === 'building_plot_graph' ? 'buildingPlotGraph' : progressPhase}`)
    : session.phase === 'cancelling' ? t('analysis:backgroundImport.cancelling')
      : session.phase === 'idle' ? '' : t(`analysis:backgroundImport.${session.phase}Label`)
  const failure = session.failure?.kind === 'task' ? importTaskRuntimeError(errorT, session.failure.event) : session.failure ? localizeUiError(errorT, { code: session.failure.code }) : undefined
  return <><Dialog open={session.dialogOpen && session.phase !== 'idle'} onOpenChange={(open) => { if (!open) { actions.hide(); setDiscardOpen(false) } }}><DialogContent aria-describedby={undefined} closeLabel={t('analysis:backgroundImport.closeDialog')}><DialogHeader><DialogTitle>{t('analysis:dialogs.importDocument')}</DialogTitle></DialogHeader>
    <div className="space-y-2"><p className="min-h-10">{phaseLabel} — {percentage}%</p><Progress aria-label={t('analysis:backgroundImport.progress')} value={percentage} /><output className="block min-h-5">{!terminal && session.progress?.received_characters != null ? session.progress.received_characters.toLocaleString() : ''}</output></div>
    {session.startedAt !== undefined && <p className="text-xs text-muted-foreground">{t('analysis:backgroundImport.elapsed', { seconds: Math.max(0, Math.floor(((session.finishedAt ?? now) - session.startedAt) / 1000)) })}</p>}
    {failure && <p role="alert">{failure.message}</p>}
    {session.phase === 'unknown' && <p>{t(session.taskId ? 'analysis:backgroundImport.unknownWithId' : 'analysis:backgroundImport.unknownWithoutId')}</p>}
    {session.actionError && <p role="alert">{t(`analysis:backgroundImport.${session.actionError}`)}</p>}
    {blockedReason && session.phase === 'completed' && <p role="status">{blockedReason}</p>}
    <DialogFooter className="flex-wrap">
      {active && <Button disabled={session.cancelIntent || !session.taskId && session.phase === 'unknown'} onClick={actions.askCancel} variant="outline">{t(session.cancelIntent ? 'analysis:backgroundImport.cancelling' : 'common:actions.cancel')}</Button>}
      {session.phase === 'unknown' && session.taskId && <Button onClick={() => void actions.reconnect()}>{t('analysis:backgroundImport.reconnect')}</Button>}
      {session.phase === 'completed' && <><Button disabled={session.discardPending} onClick={() => setDiscardOpen(true)} variant="outline">{t('analysis:backgroundImport.discard')}</Button><Button disabled={session.discardPending || Boolean(blockedReason)} onClick={() => { if (session.operationId) onOpenResult(session.operationId) }}>{t('analysis:backgroundImport.open')}</Button></>}
      {['failed', 'cancelled'].includes(session.phase) && <Button onClick={actions.acknowledgeTerminal}>{t('common:actions.ok')}</Button>}
      <Button onClick={actions.hide} variant="outline">{t('common:actions.close')}</Button>
    </DialogFooter></DialogContent></Dialog>
    <AlertDialog open={session.cancelConfirmationOpen} onOpenChange={(open) => { if (!open) actions.dismissCancel() }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:backgroundImport.cancelTitle')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:backgroundImport.cancelDescription')}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>{t('analysis:backgroundImport.continue')}</AlertDialogCancel><AlertDialogAction onClick={() => void actions.confirmCancel()}>{t('analysis:backgroundImport.confirmCancel')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={discardOpen && session.phase === 'completed'} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:backgroundImport.discardTitle')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:backgroundImport.discardDescription')}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>{t('common:actions.cancel')}</AlertDialogCancel><AlertDialogAction onClick={() => { if (session.operationId) void actions.discardResult(session.operationId); setDiscardOpen(false) }}>{t('common:actions.discard')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
