import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleHelp, CircleX, FileInput, LoaderCircle, OctagonX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useImportSession } from './ImportSessionProvider'
export const IMPORT_FILE_ACCEPT = '.fountain,.txt,.pdf,.fdx,.json,text/plain,application/pdf,application/json,application/xml,text/xml'
export function ImportAction() {
  const { t } = useTranslation('analysis')
  const { session, actions } = useImportSession()
  const input = useRef<HTMLInputElement>(null)
  const active = ['starting', 'running', 'cancelling'].includes(session.phase)
  const Badge = active ? LoaderCircle : session.phase === 'completed' ? CircleCheck : session.phase === 'unknown' ? CircleHelp : session.phase === 'cancelled' ? OctagonX : CircleX
  const label = session.phase === 'idle' ? t('dialogs.importDocument') : t('backgroundImport.statusLabel', { title: t('dialogs.importDocument'), status: t(`backgroundImport.${session.phase}`) })
  return <><Tooltip><TooltipTrigger asChild><Button aria-label={label} onClick={() => session.phase === 'idle' ? input.current?.click() : actions.show()} size="icon" variant="ghost"><span className="relative"><FileInput />{session.phase !== 'idle' && <Badge aria-hidden className={`absolute -right-2 -top-2 size-3.5 bg-background ${active ? 'animate-spin' : ''}`} />}</span></Button></TooltipTrigger><TooltipContent>{label}</TooltipContent></Tooltip><input accept={IMPORT_FILE_ACCEPT} className="hidden" id="analysis-document-import" onChange={(event) => { const file = event.target.files?.[0]; if (file) void actions.start(file); event.target.value = '' }} ref={input} type="file" /></>
}
