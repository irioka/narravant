import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { FileInput, FileOutput, Save, Sparkles, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'

import type { DocumentDetail, Scene } from '@/api/contracts'
import { deleteDocument, discardImportDraft, getDocument, getValenceSimilarities, getVersion, getVersions, reanalyzeEmotionArc, reanalyzeImportDraftEmotionArc, saveImportDraft, updateDocument } from '@/api/documents'
import { cancelTask, streamTaskProgress } from '@/api/tasks'
import { NarravantHeader } from '@/components/NarravantHeader'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { isDocumentAccessRevoked, removeDocumentAccessCache } from '@/features/documents/document-access'
import { localizeUiError } from '@/i18n/errors'
import { AnalysisContent, type AnalysisDraftUpdate } from './AnalysisContent'
import { AnalysisTabsPanel } from './AnalysisTabsPanel'
import { AudiobookPlayer } from './AudiobookPlayer'
import { DocumentList } from './DocumentList'
import { canExportNative, exportFilename, serializeNativeJson } from './export'
import { countSceneHeadings, createUtteranceRangeLookup, playbackStartAtOffset, sceneHeadingRange, type PlaybackStartPosition } from './fountain-scenes'
import { useImportSession } from './ImportSessionProvider'
import { ImportAction } from './ImportAction'
import { ImportTaskDialog } from './ImportTaskDialog'
import { createImportedDraftState, type AnalysisDraftState as DraftState } from './analysis-draft'
import { clearTextRangeMeasurement, scrollTextRangeIntoView } from './script-scroll'
import { TaskProgressDialog, type TaskProgressState } from './TaskProgressDialog'
import { ValenceSimilarityDialog } from './ValenceSimilarityDialog'
import { missingVoiceSpeakers } from './voice-assignments'
import { applyReanalysisArc } from './reanalyze-draft'

interface RuntimeErrorState { title: string; message: string }

interface DocumentPlaybackStart extends PlaybackStartPosition { documentId: string }

const IMPORT_FILE_DESCRIPTION = 'Import a .fountain, .txt, .pdf, .fdx, or Native JSON (.json) script to start analysis.'
const PROGRESS_CLOCK_INTERVAL_MS = 1000

function isDraftForRoute(draft: DraftState | undefined, documentId: string | undefined): boolean {
  return Boolean(draft && (draft.base.document_id === documentId || (!documentId && draft.importTaskId)))
}

function apiErrorMessage(t: TFunction<'errors'>, error: unknown, fallback: string): string {
  if (error instanceof Error && 'status' in error) {
    const status = (error as Error & { status: number }).status
    if (status === 404) return localizeUiError(t, { code: 'DOCUMENT_NOT_FOUND' }).message
    if (status === 409) return localizeUiError(t, { code: 'CONFLICT' }).message
    if (status === 422) return error.message
  }
  return error instanceof Error ? error.message : fallback
}

function IconAction({ children, label, onClick, destructive = false, disabled = false }: { children: React.ReactNode; label: string; onClick?: () => void; destructive?: boolean; disabled?: boolean }) {
  return <Tooltip><TooltipTrigger asChild><Button aria-label={label} disabled={disabled} onClick={onClick} size="icon" variant="ghost"><span className={destructive ? 'text-destructive' : undefined}>{children}</span></Button></TooltipTrigger><TooltipContent>{label}</TooltipContent></Tooltip>
}

// 読込み中も一覧とパネルを保持し、文書切替でスクロール位置を失わない。
function AnalysisLayout({ documentId, onBeforeNavigate, onRequestNavigate, actions, tabs, editor, content, children }: {
  documentId?: string
  onBeforeNavigate: () => void
  onRequestNavigate?: (documentId: string) => void
  actions?: React.ReactNode
  tabs?: React.ReactNode
  editor?: React.ReactNode
  content?: React.ReactNode
  children?: React.ReactNode
}) {
  return <main className="flex h-svh min-w-[1180px] flex-col overflow-hidden bg-background">
    <NarravantHeader actions={actions} />
    <Group className="flex-1" orientation="horizontal">
      <Panel defaultSize="30" minSize="24"><Group className="h-full" orientation="vertical">
        <Panel defaultSize="40" minSize="20"><aside className="h-full overflow-hidden border-b bg-card"><DocumentList activeDocumentId={documentId} onBeforeNavigate={onBeforeNavigate} onRequestNavigate={onRequestNavigate} /></aside></Panel>
        <Separator className="h-px bg-border hover:bg-primary/50" />
        <Panel defaultSize="60" minSize="20">{tabs}</Panel>
      </Group></Panel>
      <Separator className="w-px bg-border hover:bg-primary/50" />
      <Panel defaultSize="47" minSize="30">{editor}</Panel>
      <Separator className="w-px bg-border hover:bg-primary/50" />
      <Panel defaultSize="23" minSize="18"><aside className="h-full overflow-hidden bg-card">{content}</aside></Panel>
    </Group>
    {children}
  </main>
}

export function AnalysisPage() {
  const { t } = useTranslation(['analysis', 'common', 'importExport', 'errors'])
  const { t: errorT } = useTranslation('errors')
  const defaultImportTitle = t('analysis:import.defaultTitle')
  const { documentId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeSceneNumber, setActiveSceneNumber] = useState<number>()
  const [sceneJumpVersion, setSceneJumpVersion] = useState(0)
  const [similarityOpen, setSimilarityOpen] = useState(false)
  const [reanalyzeOpen, setReanalyzeOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [selectedVersionId, setSelectedVersionId] = useState<number>()
  const [draftState, setDraftState] = useState<DraftState>()
  const [historyTarget, setHistoryTarget] = useState<number | null>()
  const [historyConfirmOpen, setHistoryConfirmOpen] = useState(false)
  const [taskProgress, setTaskProgress] = useState<TaskProgressState>()
  const hasActiveTask = taskProgress !== undefined && taskProgress.terminal === undefined
  const [exportOpen, setExportOpen] = useState(false)
  const [runtimeError, setRuntimeError] = useState<RuntimeErrorState>()
  const [progressClock, setProgressClock] = useState(() => Date.now())
  const [isPlaybackActive, setIsPlaybackActive] = useState(false)
  const [playbackStopVersion, setPlaybackStopVersion] = useState(0)
  const [playbackStart, setPlaybackStart] = useState<DocumentPlaybackStart>()
  const [missingVoices, setMissingVoices] = useState<string[]>()
  const [importTitleOpen, setImportTitleOpen] = useState(false)
  const [importTitle, setImportTitle] = useState('')
  const taskAbortRef = useRef<AbortController | undefined>(undefined)
  const reanalysisSnapshotRef = useRef<{ documentId: string; currentVersionId: number; sourceFountain: string; importTaskId?: string } | undefined>(undefined)
  const stopPlayback = useCallback(() => {
    setPlaybackStopVersion((version) => version + 1)
    setIsPlaybackActive(false)
  }, [])
  const documentQuery = useQuery({ queryKey: ['document', documentId], queryFn: () => getDocument(documentId!), enabled: Boolean(documentId) })
  const versionsQuery = useQuery({ queryKey: ['document', documentId, 'versions'], queryFn: () => getVersions(documentId!), enabled: Boolean(documentId) })
  const similaritiesQuery = useQuery({ queryKey: ['document', documentId, 'valence-similarities'], queryFn: () => getValenceSimilarities(documentId!), enabled: Boolean(documentId && similarityOpen) })
  const versionQuery = useQuery({ queryKey: ['document', documentId, 'version', selectedVersionId], queryFn: () => getVersion(documentId!, selectedVersionId!), enabled: Boolean(documentId && selectedVersionId) })

  useEffect(() => {
    queryClient.removeQueries({ queryKey: ['document', undefined] })
  }, [queryClient])

  const revokedHandledRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    revokedHandledRef.current = undefined
  }, [documentId])

  const handleAccessRevoked = useCallback(async (docId: string) => {
    if (revokedHandledRef.current === docId) return
    revokedHandledRef.current = docId

    taskAbortRef.current?.abort()

    await removeDocumentAccessCache(queryClient, docId)
    await queryClient.invalidateQueries({ queryKey: ['documents'] })
    toast.error('This document is no longer available.')
    navigate('/analysis/new', { replace: true })
  }, [queryClient, navigate])

  useEffect(() => {
    if (!documentId) return
    if (isDocumentAccessRevoked(documentQuery.error)) {
      void handleAccessRevoked(documentId)
    } else if (isDocumentAccessRevoked(versionQuery.error)) {
      void handleAccessRevoked(documentId)
    }
  }, [documentId, documentQuery.error, versionQuery.error, handleAccessRevoked])
  const saveMutation = useMutation({
    mutationFn: async (titleForImportedDraft?: string) => {
      const draft = isDraftForRoute(draftState, documentId) ? draftState : undefined
      if (!draft) throw new Error('No draft available to save.')
      if (draft.importTaskId) {
        const published = await saveImportDraft(draft.importTaskId, {
          title: titleForImportedDraft?.trim() || draft.value.title,
          fountain_text: draft.value.source_fountain,
          metadata: draft.value.metadata,
          analysis: draft.value.analysis,
          emotion_arc: draft.value.emotion_arc,
          narrator: draft.value.narrator,
          voice_assignments: draft.value.voice_assignments,
        })
        return getDocument(published.document_id)
      }
      return updateDocument(documentId!, {
        expected_version: documentQuery.data!.expected_version,
        base_version_id: draft.base.version_id,
        title: draft.value.title,
        fountain_text: draft.value.source_fountain,
        metadata: draft.value.metadata,
        analysis: draft.value.analysis,
        emotion_arc: draft.value.emotion_arc,
        narrator: draft.value.narrator,
        voice_assignments: draft.value.voice_assignments,
      })
    },
    onSuccess: async (saved) => {
      setSelectedVersionId(undefined)
      setDraftState({ base: saved, value: saved })
      queryClient.setQueryData(['document', saved.document_id], saved)
      await queryClient.invalidateQueries({ queryKey: ['document', documentId, 'versions'] })
      await queryClient.invalidateQueries({ queryKey: ['documents'] })
      if (!documentId) navigate(`/analysis/${saved.document_id}`, { replace: true })
      toast.success('Changes saved.')
    },
    onError: (error) => { const saveFailed = localizeUiError(errorT, { code: 'SAVE_FAILED' }).message; setRuntimeError({ title: saveFailed, message: apiErrorMessage(errorT, error, saveFailed) }) },
  })

  const requestSave = () => {
    if (hasActiveTask) return
    stopPlayback()
    const draft = isDraftForRoute(draftState, documentId) ? draftState : undefined
    if (draft?.importTaskId) {
      setImportTitle(draft.value.title.trim() || defaultImportTitle)
      setImportTitleOpen(true)
      return
    }
    saveMutation.mutate()
  }

  const confirmImportSave = () => {
    const title = importTitle.trim()
    if (!title) return
    stopPlayback()
    setImportTitleOpen(false)
    saveMutation.mutate(title)
  }
  const deleteMutation = useMutation({ mutationFn: () => { stopPlayback(); return deleteDocument(documentId!) }, onSuccess: () => { toast.success('Document deleted.'); navigate('/analysis/new') }, onError: (error) => toast.error(apiErrorMessage(errorT, error, localizeUiError(errorT, { code: 'DELETE_FAILED' }).message)) })
  const reanalyzeMutation = useMutation({
    mutationFn: async () => {
      const snapshot = reanalysisSnapshotRef.current
      if (!snapshot) throw new Error('Reanalysis requires the active document draft.')
      if (!snapshot.importTaskId && snapshot.documentId !== documentId) {
        throw new Error('Reanalysis requires the active saved document snapshot.')
      }
      const accepted = snapshot.importTaskId
        ? await reanalyzeImportDraftEmotionArc(snapshot.importTaskId, snapshot.sourceFountain)
        : await reanalyzeEmotionArc(snapshot.documentId, snapshot.sourceFountain)
      const controller = new AbortController()
      taskAbortRef.current = controller
      setTaskProgress({
        taskId: accepted.task_id,
        operation: 'reanalyze',
        phase: 'queued',
        percentage: 0,
        startedAt: Date.now(),
      })
      void streamTaskProgress(accepted.task_id, {
        onProgress: ({ phase, percentage, received_characters }) => {
          setTaskProgress((current) =>
            current?.taskId === accepted.task_id
              ? {
                ...current,
                phase,
                percentage,
                receivedCharacters: received_characters,
                cancelRequestPending: phase === 'cancelling' ? false : current.cancelRequestPending,
              }
              : current,
          )
        },
        onCompleted: (completed) => {
          if (!completed.reanalysis) {
            setTaskProgress((current) =>
              current?.taskId === accepted.task_id
                ? { ...current, cancelRequestPending: false, terminal: 'error' }
                : current,
            )
            setRuntimeError({
              title: t('analysis:runtime.reanalyzeIncompleteTitle'),
              message: t('analysis:runtime.reanalyzeIncompleteMessage'),
            })
            return
          }
          setTaskProgress((current) =>
            current?.taskId === accepted.task_id
              ? {
                ...current,
                phase: 'completed',
                percentage: 100,
                cancelRequestPending: false,
                terminal: 'completed',
              }
              : current,
          )
          const emotionArc = completed.reanalysis.emotion_arc
          if (completed.document_id !== snapshot.documentId || completed.version_id !== snapshot.currentVersionId) return
          setDraftState((current) => {
            if (
              !current
              || current.base.document_id !== snapshot.documentId
              || current.base.current_version_id !== snapshot.currentVersionId
              || current.value.source_fountain !== snapshot.sourceFountain
            ) return current
            return { ...current, value: { ...current.value, ...applyReanalysisArc(current.value, emotionArc) } }
          })
        },
        onError: (error) => {
          setTaskProgress((current) =>
            current?.taskId === accepted.task_id
              ? { ...current, cancelRequestPending: false, terminal: 'error' }
              : current,
          )
          setRuntimeError({ title: localizeUiError(errorT, { code: 'REANALYSIS_FAILED' }).message, message: error.message })
        },
        onCancelled: () => {
          setTaskProgress((current) =>
            current?.taskId === accepted.task_id
              ? { ...current, phase: 'cancelled', cancelRequestPending: false, terminal: 'cancelled' }
              : current,
          )
        },
      }, undefined, controller.signal).catch((error: unknown) => {
        if (!controller.signal.aborted) {
          const message = error instanceof Error ? error.message : localizeUiError(errorT, { code: 'SSE_DISCONNECTED' }).message
          setTaskProgress((current) =>
            current?.taskId === accepted.task_id
              ? { ...current, cancelRequestPending: false, terminal: 'error' }
              : current,
          )
          setRuntimeError({ title: t('analysis:runtime.reanalyzeConnectionTitle'), message })
        }
      })
    },
    onSuccess: () => { setReanalyzeOpen(false); toast.success('Emotional Arc reanalysis started.') },
    onError: (error) => toast.error(apiErrorMessage(errorT, error, localizeUiError(errorT, { code: 'REANALYSIS_FAILED' }).message)),
  })
  const loadedDocument = selectedVersionId ? versionQuery.data : documentQuery.data
  useEffect(() => {
    if (!loadedDocument) return
    queueMicrotask(() => setDraftState((current) => {
      if (current?.base.document_id === loadedDocument.document_id && current.base.version_id === loadedDocument.version_id) return current
      return { base: loadedDocument, value: loadedDocument, pendingCurrentVersionId: selectedVersionId }
    }))
  }, [loadedDocument, selectedVersionId])
  const activeDraftState = isDraftForRoute(draftState, documentId) ? draftState : undefined
  const document = activeDraftState?.value ?? loadedDocument
  const isDirty = useMemo(() => {
    const editableDraft = activeDraftState && { title: activeDraftState.value.title, source_fountain: activeDraftState.value.source_fountain, metadata: activeDraftState.value.metadata, analysis: activeDraftState.value.analysis, emotion_arc: activeDraftState.value.emotion_arc, narrator: activeDraftState.value.narrator, voice_assignments: activeDraftState.value.voice_assignments }
    const editableBase = activeDraftState && { title: activeDraftState.base.title, source_fountain: activeDraftState.base.source_fountain, metadata: activeDraftState.base.metadata, analysis: activeDraftState.base.analysis, emotion_arc: activeDraftState.base.emotion_arc, narrator: activeDraftState.base.narrator, voice_assignments: activeDraftState.base.voice_assignments }
    return Boolean(activeDraftState && (!activeDraftState.value.is_saved || JSON.stringify(editableDraft) !== JSON.stringify(editableBase) || activeDraftState.pendingCurrentVersionId !== undefined))
  }, [activeDraftState])
  const updateDraft = (update: AnalysisDraftUpdate & Partial<Pick<DocumentDetail, 'emotion_arc' | 'source_fountain'>>) => {
    if (isPlaybackActive) stopPlayback()
    setDraftState((current) => current && isDraftForRoute(current, documentId) ? { ...current, value: { ...current.value, ...update, metadata: update.metadata ?? current.value.metadata, analysis: update.analysis ?? current.value.analysis, emotion_arc: update.emotion_arc ?? current.value.emotion_arc, source_fountain: update.source_fountain ?? current.value.source_fountain } } : current)
  }
  const confirmReanalysis = () => {
    if (!document) return
    reanalysisSnapshotRef.current = {
      documentId: document.document_id,
      currentVersionId: document.current_version_id,
      sourceFountain: document.source_fountain,
      importTaskId: activeDraftState?.importTaskId,
    }
    reanalyzeMutation.mutate()
  }
  const selectHistoryVersion = (versionId: number) => {
    stopPlayback()
    const target = versionId === documentQuery.data?.version_id ? null : versionId
    if (isDirty) {
      setHistoryTarget(target)
      setHistoryConfirmOpen(true)
      return
    }
    setSelectedVersionId(target ?? undefined)
  }
  const discardAndSwitchHistory = async () => {
    try {
      if (activeDraftState?.importTaskId) {
        await discardImportDraft(activeDraftState.importTaskId)
        setDraftState(documentQuery.data ? { base: documentQuery.data, value: documentQuery.data } : undefined)
        setHistoryConfirmOpen(false)
        return
      }
      setDraftState(undefined)
      setSelectedVersionId(historyTarget ?? undefined)
      setHistoryConfirmOpen(false)
    } catch (error) {
      const discardFailed = localizeUiError(errorT, { code: 'DISCARD_FAILED' }).message; setRuntimeError({ title: discardFailed, message: apiErrorMessage(errorT, error, discardFailed) })
    }
  }
  const selectedScene = useMemo(() => document?.scenes.find((scene) => scene.scene_number === activeSceneNumber), [activeSceneNumber, document?.scenes])

  useEffect(() => () => { taskAbortRef.current?.abort() }, [])
  useEffect(() => {
    if (!hasActiveTask) return
    const timer = window.setInterval(() => setProgressClock(Date.now()), PROGRESS_CLOCK_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [hasActiveTask])
  const { session: importSession, actions: importActions } = useImportSession()
  const [interaction, setInteraction] = useState<{ documentId: string; busy: boolean; editorOpen: boolean }>()
  const [switchRequest, setSwitchRequest] = useState<{ operationId?: string; targetId?: string; currentDocumentId?: string; currentImportTaskId?: string; draft?: DraftState }>()
  const switching = useRef(false)
  const [switchPending, setSwitchPending] = useState(false)
  const interactionBlocked = interaction?.documentId === document?.document_id && (interaction?.busy || interaction?.editorOpen)
  const applicationBlocked = Boolean(saveMutation.isPending || reanalyzeMutation.isPending || hasActiveTask || interactionBlocked || importTitleOpen || reanalyzeOpen || deleteMutation.isPending)
  const switchBlocked = applicationBlocked || switchPending
  const currentSnapshot = useRef({ documentId, draft: activeDraftState, switchBlocked: applicationBlocked, operationId: importSession.operationId })
  useEffect(() => {
    currentSnapshot.current = { documentId, draft: activeDraftState, switchBlocked: applicationBlocked, operationId: importSession.operationId }
  })
  const applySwitch = async (request: NonNullable<typeof switchRequest>) => {
    const latest = () => {
      const current = currentSnapshot.current
      return current.documentId === request.currentDocumentId && current.draft === request.draft
        && !current.switchBlocked && (!request.operationId || current.operationId === request.operationId)
    }
    if (switching.current || !latest()) { setSwitchRequest(undefined); return }
    switching.current = true
    // 応答待ちは操作を止めるが、確認前・取消時には再生と結果を変更しない。
    try {
      if (request.currentImportTaskId) {
        setSwitchPending(true)
        try { await discardImportDraft(request.currentImportTaskId) } catch (error) {
          if (!(error instanceof Error && 'status' in error && error.status === 404)) throw error
        }
        // 自分自身のpending以外の変更を再確認する。
        if (!latest()) { setSwitchRequest(undefined); return }
      }
      if (request.operationId) {
        const pending = importActions.consumeResult(request.operationId)
        if (!pending) return
        const next = createImportedDraftState(pending.draft, pending.taskId, defaultImportTitle, new Date().toISOString())
        stopPlayback(); setActiveSceneNumber(undefined); setSelectedVersionId(undefined)
        setDraftState(next)
        if (documentId) navigate('/analysis/new', { replace: true })
      } else if (request.targetId) {
        stopPlayback(); setDraftState(undefined); setSelectedVersionId(undefined); setActiveSceneNumber(undefined)
        navigate(`/analysis/${request.targetId}`)
      }
      setSwitchRequest(undefined)
    } catch {
      setSwitchRequest(undefined)
      const message = localizeUiError(errorT, { code: 'DISCARD_FAILED' }).message
      setRuntimeError({ title: message, message })
    } finally { switching.current = false; setSwitchPending(false) }
  }
  const requestSwitch = (target: { operationId?: string; targetId?: string }) => {
    if (switchBlocked || switching.current) return
    if (target.operationId && (importSession.phase !== 'completed' || importSession.operationId !== target.operationId || importSession.discardPending)) return
    if (target.targetId && target.targetId === documentId) return
    const request = { ...target, currentDocumentId: documentId, currentImportTaskId: activeDraftState?.importTaskId, draft: activeDraftState }
    if (isDirty || isPlaybackActive || activeDraftState?.importTaskId) setSwitchRequest(request)
    else void applySwitch(request)
  }
  const importDialog = <><ImportTaskDialog blockedReason={switchBlocked ? t('analysis:backgroundImport.wait') : undefined} onOpenResult={(operationId) => requestSwitch({ operationId })} />
    <AlertDialog open={Boolean(switchRequest)} onOpenChange={(open) => { if (!open && !switching.current) setSwitchRequest(undefined) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:backgroundImport.switchTitle')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:backgroundImport.switchDescription')}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={switchPending} onClick={() => { if (switchRequest?.operationId) importActions.hide() }}>{t('analysis:backgroundImport.back')}</AlertDialogCancel><AlertDialogAction disabled={Boolean(switchBlocked)} onClick={() => { if (switchRequest) void applySwitch(switchRequest) }}>{t(switchRequest?.operationId ? 'analysis:backgroundImport.switchConfirm' : 'analysis:backgroundImport.navigateConfirm')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog></>

  const cancelActiveTask = useCallback(async () => {
    const taskId = taskProgress?.taskId
    if (
      !taskId
      || taskProgress.terminal
      || taskProgress.phase === 'cancelling'
      || taskProgress.cancelRequestPending
    ) return

    const previousPhase = taskProgress.phase
    setTaskProgress((current) => current?.taskId === taskId
      ? { ...current, phase: 'cancelling', cancelRequestPending: true }
      : current)

    try {
      await cancelTask(taskId)
    } catch (error) {
      setTaskProgress((current) => (
        current?.taskId === taskId && current.cancelRequestPending
          ? { ...current, phase: previousPhase, cancelRequestPending: false }
          : current
      ))
      toast.error(error instanceof Error ? error.message : localizeUiError(errorT, { code: 'HTTP_ERROR' }).message)
    }
  }, [errorT, taskProgress])

  const dismissTaskProgress = useCallback(() => {
    if (taskProgress?.terminal) setTaskProgress(undefined)
  }, [taskProgress?.terminal])

  const withImportDialog = (workspace: React.ReactNode) => <>{workspace}{importDialog}</>
  if (!documentId && !document) return withImportDialog(<><EmptyAnalysis /><TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} /><RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} /></>)
  if (!document && (documentQuery.isPending || (selectedVersionId !== undefined && versionQuery.isPending))) return withImportDialog(<AnalysisLayout actions={<ImportAction />} documentId={documentId} onBeforeNavigate={stopPlayback} onRequestNavigate={(targetId) => requestSwitch({ targetId })} editor={<div className="grid h-full place-items-center text-sm text-muted-foreground">Loading document…</div>}><TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} /><RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} /></AnalysisLayout>)
  if ((!activeDraftState && (documentQuery.isError || (selectedVersionId !== undefined && versionQuery.isError))) || !document) return withImportDialog(<><AnalysisLayout actions={<ImportAction />} documentId={documentId} onBeforeNavigate={stopPlayback} onRequestNavigate={(targetId) => requestSwitch({ targetId })} editor={<div className="text-center"><p className="text-sm text-muted-foreground">Unable to load document.</p><Button asChild className="mt-4" variant="outline"><Link to="/analysis/new">{t('common:actions.retry')}</Link></Button></div>} /><TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} /><RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} /></>)

  const canReanalyze = !selectedVersionId
    && document.analysis.status === 'completed'
    && document.capabilities.can_edit
    && !isPlaybackActive
    && Boolean(document.source_fountain.trim())
    && !saveMutation.isPending
    && !reanalyzeMutation.isPending
    && !taskProgress

  return withImportDialog(<AnalysisLayout documentId={documentId} onBeforeNavigate={stopPlayback} onRequestNavigate={(targetId) => requestSwitch({ targetId })}
    actions={<><ImportAction /><IconAction label={t('importExport:export.title')} onClick={() => { stopPlayback(); setExportOpen(true) }}><FileOutput /></IconAction><IconAction disabled={!document.capabilities.can_delete} destructive label={t('common:actions.delete')} onClick={() => { stopPlayback(); setDeleteOpen(true) }}><Trash2 /></IconAction></>}
    tabs={<AnalysisTabsPanel activeSceneNumber={activeSceneNumber} document={document} onDraftChange={!isPlaybackActive ? updateDraft : undefined} onOpenSimilarities={() => setSimilarityOpen(true)} onReanalyze={canReanalyze ? () => setReanalyzeOpen(true) : undefined} onSceneSelect={(sceneNumber) => { stopPlayback(); setActiveSceneNumber(sceneNumber); setSceneJumpVersion((version) => version + 1); setPlaybackStart({ documentId: document.document_id, sceneNumber, utteranceIndex: 0 }) }} onSelectVersion={selectHistoryVersion} titleMode={document.capabilities.can_edit && !isPlaybackActive ? 'editable' : 'readonly'} versions={versionsQuery.data?.items ?? []} />}
    editor={<EditorPane activeScene={selectedScene} editable={Boolean(document.capabilities.can_edit && !isPlaybackActive)} isSaving={saveMutation.isPending} onChange={(source_fountain) => updateDraft({ source_fountain })} onCursorPositionChange={(position) => setPlaybackStart(position ? { ...position, documentId: document.document_id } : undefined)} onSave={requestSave} isPlaybackActive={isPlaybackActive} player={(onPositionChange) => <AudiobookPlayer documentId={document.document_id} draft={activeDraftState?.importTaskId || isDirty ? { source_fountain: document.source_fountain, voice_assignments: document.voice_assignments ?? [] } : undefined} isPlaying={isPlaybackActive} onBeforePlay={() => { const missing = missingVoiceSpeakers(document); if (missing.length > 0) { setMissingVoices(missing); return false } return true }} onPlaybackPositionChange={onPositionChange} onPlayingChange={setIsPlaybackActive} onSceneJump={(sceneNumber) => { stopPlayback(); setActiveSceneNumber(sceneNumber); setSceneJumpVersion((version) => version + 1); setPlaybackStart({ documentId: document.document_id, sceneNumber, utteranceIndex: 0 }) }} playbackStart={playbackStart?.documentId === document.document_id ? playbackStart : undefined} scenes={document.scenes} stopSignal={playbackStopVersion} />} saveEnabled={Boolean(document.capabilities.can_edit && isDirty && !isPlaybackActive && !hasActiveTask)} sceneJumpVersion={sceneJumpVersion} value={document.source_fountain} />}
    content={<AnalysisContent onInteractionStateChange={setInteraction} document={document} isDraft={Boolean(activeDraftState?.importTaskId)} onDraftChange={!isPlaybackActive ? updateDraft : undefined} titleMode={document.capabilities.can_edit && !isPlaybackActive ? 'editable' : 'readonly'} />}
  >
    <ValenceSimilarityDialog items={similaritiesQuery.data?.items.map((item) => ({ label: item.name, value: item.percentage.toFixed(2) })) ?? []} onOpenChange={setSimilarityOpen} open={similarityOpen} />
    <ReanalyzeDialog isPending={reanalyzeMutation.isPending} onConfirm={confirmReanalysis} onOpenChange={setReanalyzeOpen} open={reanalyzeOpen} />
    <ImportTitleDialog isPending={saveMutation.isPending} onConfirm={confirmImportSave} onOpenChange={setImportTitleOpen} onTitleChange={setImportTitle} open={importTitleOpen} title={importTitle} />
    <AlertDialog onOpenChange={setHistoryConfirmOpen} open={historyConfirmOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle><AlertDialogDescription>Choose a history version only after discarding the current unsaved draft.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => { void discardAndSwitchHistory() }}>Discard and switch</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog onOpenChange={setDeleteOpen} open={deleteOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:dialogs.deleteTitle')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:dialogs.deleteDescription', { title: document.title })}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>{t('common:actions.cancel')}</AlertDialogCancel><AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>{t('common:actions.delete')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <ExportDialog document={document} onOpenChange={setExportOpen} open={exportOpen} />
    <TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} />
    <RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} />
    <MissingVoicesDialog onDismiss={() => setMissingVoices(undefined)} speakers={missingVoices} />
  </AnalysisLayout>)
}

function MissingVoicesDialog({ onDismiss, speakers }: { onDismiss: () => void; speakers: string[] | undefined }) {
  const { t } = useTranslation(['analysis', 'common'])
  return <AlertDialog onOpenChange={(open) => { if (!open) onDismiss() }} open={Boolean(speakers && speakers.length > 0)}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:voices.missingVoicesTitle')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:voices.missingVoicesBody')}</AlertDialogDescription></AlertDialogHeader><ul className="list-disc pl-5 text-sm text-muted-foreground">{(speakers ?? []).map((speaker) => <li key={speaker}>{speaker}</li>)}</ul><AlertDialogFooter><AlertDialogAction onClick={onDismiss}>{t('common:actions.close')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}

function RuntimeErrorDialog({ error, onDismiss }: { error: RuntimeErrorState | undefined; onDismiss: () => void }) {
  return <AlertDialog onOpenChange={(open) => { if (!open) onDismiss() }} open={Boolean(error)}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{error?.title}</AlertDialogTitle><AlertDialogDescription>{error?.message}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogAction onClick={onDismiss}>Close</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}

function ImportTitleDialog({ isPending, onConfirm, onOpenChange, onTitleChange, open, title }: { isPending: boolean; onConfirm: () => void; onOpenChange: (open: boolean) => void; onTitleChange: (title: string) => void; open: boolean; title: string }) {
  const { t } = useTranslation(['analysis', 'common'])
  return <Dialog onOpenChange={onOpenChange} open={open}><DialogContent><DialogHeader><DialogTitle>{t('analysis:dialogs.importSaveTitle')}</DialogTitle><DialogDescription>{t('analysis:dialogs.importSaveDescription')}</DialogDescription></DialogHeader><Input aria-label={t('analysis:dialogs.importSaveTitle')} autoFocus onChange={(event) => onTitleChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && title.trim()) onConfirm() }} value={title} /><DialogFooter><Button disabled={isPending} onClick={() => onOpenChange(false)} variant="outline">{t('common:actions.cancel')}</Button><Button disabled={isPending || !title.trim()} onClick={onConfirm}>{isPending ? t('analysis:editor.saving') : t('common:actions.save')}</Button></DialogFooter></DialogContent></Dialog>
}

export function EmptyAnalysis() {
  const { t } = useTranslation(['analysis', 'importExport'])
  return <AnalysisLayout actions={<ImportAction />} onBeforeNavigate={() => undefined}
    tabs={<section className="grid h-full place-items-center bg-card p-6 text-sm text-muted-foreground">{t('importExport:import.begin')}</section>}
    editor={<section className="grid h-full place-items-center bg-background p-7 text-center text-sm text-muted-foreground"><div><FileInput className="mx-auto mb-3 size-6 text-primary" /><p>{IMPORT_FILE_DESCRIPTION}</p></div></section>}
    content={<div className="grid h-full place-items-center p-6 text-sm text-muted-foreground"><Sparkles />{t('importExport:import.begin')}</div>} />
}

function downloadExport(filename: string, content: string, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const anchor = window.document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function ExportDialog({ document, onOpenChange, open }: { document: DocumentDetail; onOpenChange: (open: boolean) => void; open: boolean }) {
  const { t } = useTranslation(['common', 'importExport'])
  const nativeAvailable = canExportNative(document)
  const exportFountain = () => {
    downloadExport(exportFilename(document.title, 'fountain'), document.source_fountain, 'text/plain;charset=utf-8')
    onOpenChange(false)
  }
  const exportNative = () => {
    downloadExport(exportFilename(document.title, 'json'), `${JSON.stringify(serializeNativeJson(document), null, 2)}\n`, 'application/json;charset=utf-8')
    onOpenChange(false)
  }
  return <Dialog onOpenChange={onOpenChange} open={open}><DialogContent><DialogHeader><DialogTitle>{t('importExport:export.title')}</DialogTitle><DialogDescription>{t('importExport:export.description')}</DialogDescription></DialogHeader><div className="grid gap-3"><Button onClick={exportFountain} variant="outline">{t('importExport:export.fountain')}</Button><Tooltip><TooltipTrigger asChild><span><Button className="w-full" disabled={!nativeAvailable} onClick={exportNative} variant="outline">{t('importExport:export.native')}</Button></span></TooltipTrigger>{!nativeAvailable && <TooltipContent>{t('importExport:export.nativeUnavailable')}</TooltipContent>}</Tooltip></div><DialogFooter><Button onClick={() => onOpenChange(false)} variant="ghost">{t('common:actions.close')}</Button></DialogFooter></DialogContent></Dialog>
}

interface EditorPaneProps {
  activeScene?: Scene
  editable: boolean
  isPlaybackActive: boolean
  isSaving: boolean
  onChange: (text: string) => void
  onCursorPositionChange: (position: PlaybackStartPosition | undefined) => void
  onSave: () => void
  player?: (onPositionChange: (position: PlaybackStartPosition | undefined) => void) => React.ReactNode
  saveEnabled: boolean
  sceneJumpVersion: number
  value: string
}

function EditorPane({ activeScene, editable, isPlaybackActive, isSaving, onChange, onCursorPositionChange, onSave, player, saveEnabled, sceneJumpVersion, value }: EditorPaneProps) {
  const { t } = useTranslation(['analysis', 'common'])
  // 発話ごとの位置更新で、一覧・分析欄を含むAnalysisPage全体を再描画しない。
  const [playbackPosition, setPlaybackPosition] = useState<PlaybackStartPosition>()
  const updatePlaybackPosition = useCallback((position: PlaybackStartPosition | undefined) => {
    setPlaybackPosition((current) => current?.sceneNumber === position?.sceneNumber
      && current?.utteranceIndex === position?.utteranceIndex
      && current?.sourceOffset === position?.sourceOffset ? current : position)
  }, [])
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const lookupUtterance = useMemo(() => createUtteranceRangeLookup(value), [value])
  const sceneCount = useMemo(() => countSceneHeadings(value), [value])
  useEffect(() => {
    const element = textAreaRef.current
    return () => { if (element) clearTextRangeMeasurement(element) }
  }, [value, editable])
  const valueRef = useRef(value)
  const lastPlaybackRangeRef = useRef<{ source: string; start: number; end: number } | undefined>(undefined)
  useEffect(() => {
    valueRef.current = value
  }, [value])
  useEffect(() => {
    const source = valueRef.current
    const sceneRange = activeScene ? sceneHeadingRange(source, activeScene.scene_number) : undefined
    const element = textAreaRef.current
    if (!sceneRange || !element) return
    element.focus({ preventScroll: true })
    element.setSelectionRange(sceneRange.start, sceneRange.end)
    scrollTextRangeIntoView(element, source, sceneRange)
  }, [activeScene, sceneJumpVersion])
  useEffect(() => {
    const source = valueRef.current
    const element = textAreaRef.current
    if (!isPlaybackActive || !playbackPosition || !element) {
      lastPlaybackRangeRef.current = undefined
      return
    }
    const range = lookupUtterance(playbackPosition.sceneNumber, playbackPosition.utteranceIndex, playbackPosition.sourceOffset)
    if (!range) return
    // 同じ発話のPCMごとに再選択・再スクロールしない。
    const previous = lastPlaybackRangeRef.current
    if (previous?.source === source && previous.start === range.start && previous.end === range.end) return
    element.focus({ preventScroll: true })
    element.setSelectionRange(range.start, range.end)
    scrollTextRangeIntoView(element, source, range)
    lastPlaybackRangeRef.current = { source, ...range }
  }, [isPlaybackActive, playbackPosition, lookupUtterance])
  const updateCursorPosition = (source: string, cursorOffset: number) => {
    if (!editable) return
    onCursorPositionChange(playbackStartAtOffset(source, cursorOffset))
  }
  return <section className="flex h-full flex-col">{player && <div className="border-b p-2">{player(updatePlaybackPosition)}</div>}<Textarea aria-label={t('analysis:editor.scriptBody')} className="min-h-0 flex-1 resize-none rounded-none border-0 bg-background p-7 font-mono text-sm leading-7 shadow-none focus-visible:ring-0" onChange={(event) => { onChange(event.target.value); updateCursorPosition(event.target.value, event.currentTarget.selectionStart) }} onClick={(event) => updateCursorPosition(value, event.currentTarget.selectionStart)} onKeyDown={(event) => { if (!editable) event.preventDefault() }} onKeyUp={(event) => updateCursorPosition(value, event.currentTarget.selectionStart)} onMouseDown={(event) => { if (!editable) event.preventDefault() }} onSelect={(event) => updateCursorPosition(value, event.currentTarget.selectionStart)} readOnly={!editable} ref={textAreaRef} value={value} /><div className="grid shrink-0 grid-cols-3 items-center border-t bg-card p-3"><span className="text-xs text-muted-foreground">{t('analysis:sceneCount', { count: sceneCount })}</span><Button className="justify-self-center" disabled={!saveEnabled || isSaving} onClick={onSave} size="sm" variant="outline"><Save />{isSaving ? t('analysis:editor.saving') : t('common:actions.save')}</Button></div></section>
}
function ReanalyzeDialog({ isPending, onConfirm, onOpenChange, open }: { isPending: boolean; onConfirm: () => void; onOpenChange: (open: boolean) => void; open: boolean }) {
  return <Dialog onOpenChange={onOpenChange} open={open}><DialogContent><DialogHeader><DialogTitle>Reanalyze Emotional Arc</DialogTitle></DialogHeader><DialogFooter><Button onClick={() => onOpenChange(false)} variant="outline">Cancel</Button><Button disabled={isPending} onClick={onConfirm}>{isPending ? 'Starting…' : 'Reanalyze'}</Button></DialogFooter></DialogContent></Dialog>
}
