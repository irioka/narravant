import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { FileInput, FileOutput, Save, Sparkles, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'

import type { DocumentDetail, Scene } from '@/api/contracts'
import { deleteDocument, discardImportDraft, getDocument, getValenceSimilarities, getVersion, getVersions, importDocument, reanalyzeEmotionArc, saveImportDraft, updateDocument } from '@/api/documents'
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
import { countSceneHeadings, playbackStartAtOffset, sceneHeadingRange, utteranceRange, type PlaybackStartPosition } from './fountain-scenes'
import { importTaskRuntimeError } from './import-error'
import { scrollTextRangeIntoView } from './script-scroll'
import { TaskProgressDialog, type TaskProgressState } from './TaskProgressDialog'
import { ValenceSimilarityDialog } from './ValenceSimilarityDialog'
import { missingVoiceSpeakers } from './voice-assignments'

interface RuntimeErrorState { title: string; message: string }
interface DraftState { base: DocumentDetail; value: DocumentDetail; pendingCurrentVersionId?: number; importTaskId?: string }
interface DocumentPlaybackStart extends PlaybackStartPosition { documentId: string }

const IMPORT_FILE_ACCEPT = '.fountain,.txt,.pdf,.fdx,.json,text/plain,application/pdf,application/json,application/xml,text/xml'
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

export function AnalysisPage() {
  const { t } = useTranslation(['analysis', 'common', 'importExport', 'errors'])
  const errorT = t as unknown as TFunction<'errors'>
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
  const [exportOpen, setExportOpen] = useState(false)
  const [runtimeError, setRuntimeError] = useState<RuntimeErrorState>()
  const [progressClock, setProgressClock] = useState(() => Date.now())
  const [isPlaybackActive, setIsPlaybackActive] = useState(false)
  const [playbackStopVersion, setPlaybackStopVersion] = useState(0)
  const [playbackStart, setPlaybackStart] = useState<DocumentPlaybackStart>()
  const [playbackPosition, setPlaybackPosition] = useState<PlaybackStartPosition>()
  const [missingVoices, setMissingVoices] = useState<string[]>()
  const [importTitleOpen, setImportTitleOpen] = useState(false)
  const [importTitle, setImportTitle] = useState('')
  const taskAbortRef = useRef<AbortController | undefined>(undefined)
  const stopPlayback = useCallback(() => {
    setPlaybackStopVersion((version) => version + 1)
    setIsPlaybackActive(false)
    setPlaybackPosition(undefined)
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
      const accepted = await reanalyzeEmotionArc(documentId!)
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
          setDraftState((current) => current ? { ...current, value: { ...current.value, emotion_arc: emotionArc } } : current)
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
  const editableDraft = activeDraftState && { title: activeDraftState.value.title, source_fountain: activeDraftState.value.source_fountain, metadata: activeDraftState.value.metadata, analysis: activeDraftState.value.analysis, emotion_arc: activeDraftState.value.emotion_arc, narrator: activeDraftState.value.narrator, voice_assignments: activeDraftState.value.voice_assignments }
  const editableBase = activeDraftState && { title: activeDraftState.base.title, source_fountain: activeDraftState.base.source_fountain, metadata: activeDraftState.base.metadata, analysis: activeDraftState.base.analysis, emotion_arc: activeDraftState.base.emotion_arc, narrator: activeDraftState.base.narrator, voice_assignments: activeDraftState.base.voice_assignments }
  const isDirty = Boolean(activeDraftState && (!activeDraftState.value.is_saved || JSON.stringify(editableDraft) !== JSON.stringify(editableBase) || activeDraftState.pendingCurrentVersionId !== undefined))
  const updateDraft = (update: AnalysisDraftUpdate & Partial<Pick<DocumentDetail, 'emotion_arc' | 'source_fountain'>>) => {
    if (isPlaybackActive) stopPlayback()
    setDraftState((current) => current && isDraftForRoute(current, documentId) ? { ...current, value: { ...current.value, ...update, metadata: update.metadata ?? current.value.metadata, analysis: update.analysis ?? current.value.analysis, emotion_arc: update.emotion_arc ?? current.value.emotion_arc, source_fountain: update.source_fountain ?? current.value.source_fountain } } : current)
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
  const hasActiveTask = taskProgress !== undefined && taskProgress.terminal === undefined
  useEffect(() => {
    if (!hasActiveTask) return
    const timer = window.setInterval(() => setProgressClock(Date.now()), PROGRESS_CLOCK_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [hasActiveTask])
  const importFile = useCallback(async (file: File | undefined) => {
    if (!file) return
    stopPlayback()
    const startedAt = Date.now()
    setTaskProgress({ operation: 'import', phase: 'reading', percentage: 5, startedAt, terminal: undefined })
    try {
      const accepted = await importDocument(file)
      const controller = new AbortController()
      taskAbortRef.current = controller
      setTaskProgress({ taskId: accepted.task_id, operation: 'import', phase: 'queued', percentage: 0, startedAt, terminal: undefined })
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
        onCompleted: async (completed) => {
          const importedDraft = completed.import_draft
          if (!importedDraft) {
            setTaskProgress((current) =>
              current?.taskId === accepted.task_id
                ? { ...current, cancelRequestPending: false, terminal: 'error' }
                : current,
            )
            setRuntimeError({ title: t('analysis:runtime.importIncompleteTitle'), message: t('analysis:runtime.importIncompleteMessage') })
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
          const now = new Date().toISOString()
          const value: DocumentDetail = {
            ...importedDraft,
            title: importedDraft.title.trim() || defaultImportTitle,
            analysis: {
              ...importedDraft.analysis,
              characters: importedDraft.analysis.characters.map((character) => ({
                ...character,
                external_goal: character.external_goal ?? null,
                internal_need: character.internal_need ?? null,
                fear_or_cost: character.fear_or_cost ?? null,
                obstacle: character.obstacle ?? null,
                choice: character.choice ?? null,
                agency: character.agency ?? null,
                goal_to_outcome: character.goal_to_outcome ?? null,
              })),
            },
            owner_user_id: documentQuery.data?.owner_user_id ?? '',
            current_version_id: importedDraft.version_id,
            expected_version: importedDraft.expected_version ?? 1,
            is_saved: false,
            created_at: documentQuery.data?.created_at ?? now,
            updated_at: now,
            owner_email: documentQuery.data?.owner_email ?? '',
            shared_count: documentQuery.data?.shared_count ?? 0,
            gcs_uri: null,
            generation: null,
            capabilities: documentQuery.data?.capabilities ?? { can_edit: true, can_share: false, can_delete: false },
          }
          setActiveSceneNumber(undefined)
          setSelectedVersionId(undefined)
          setDraftState({ base: value, value, importTaskId: accepted.task_id })
          // Keep unsaved imports on the new-document route. The generated id is
          // draft-only until Save, so it must not trigger document GET/versions
          // or saved-document voice authorization requests.
          if (documentId) navigate('/analysis/new', { replace: true })
        },
        onError: (error) => {
          setTaskProgress((current) =>
            current?.taskId === accepted.task_id
              ? { ...current, cancelRequestPending: false, terminal: 'error' }
              : current,
          )
          setRuntimeError(importTaskRuntimeError(errorT, error))
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
          setRuntimeError({ title: t('analysis:runtime.importConnectionTitle'), message })
        }
      })
    } catch (error) {
      setTaskProgress(undefined)
      const startFailed = t('importExport:import.startFailed'); setRuntimeError({ title: startFailed, message: apiErrorMessage(errorT, error, startFailed) })
    }
  }, [defaultImportTitle, documentId, documentQuery.data, errorT, navigate, stopPlayback, t])

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

  if (!documentId && !document) return <><EmptyAnalysis onImport={importFile} /><TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} /><RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} /></>
  if (!document && (documentQuery.isPending || (selectedVersionId !== undefined && versionQuery.isPending))) return <><main className="grid h-svh place-items-center bg-background text-sm text-muted-foreground">Loading document…</main><TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} /><RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} /></>
  if ((!activeDraftState && (documentQuery.isError || (selectedVersionId !== undefined && versionQuery.isError))) || !document) return <><main className="grid h-svh place-items-center bg-background"><div className="text-center"><p className="text-sm text-muted-foreground">Unable to load document.</p><Button asChild className="mt-4" variant="outline"><Link to="/analysis/new">{t('common:actions.retry')}</Link></Button></div></main><TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} /><RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} /></>

  return <main className="flex h-svh min-w-[1180px] flex-col overflow-hidden bg-background">
    <NarravantHeader actions={<><IconAction disabled={!document.capabilities.can_edit || Boolean(activeDraftState?.importTaskId)} label={t('analysis:dialogs.importDocument')} onClick={() => { stopPlayback(); window.document.getElementById('analysis-document-import')?.click() }}><FileInput /></IconAction><IconAction label={t('importExport:export.title')} onClick={() => { stopPlayback(); setExportOpen(true) }}><FileOutput /></IconAction><IconAction disabled={!document.capabilities.can_delete} destructive label={t('common:actions.delete')} onClick={() => { stopPlayback(); setDeleteOpen(true) }}><Trash2 /></IconAction><input accept={IMPORT_FILE_ACCEPT} className="hidden" disabled={!document.capabilities.can_edit} onChange={(event) => { void importFile(event.target.files?.[0]); event.target.value = '' }} type="file" id="analysis-document-import" /></>} />
    <Group className="flex-1" orientation="horizontal"><Panel defaultSize="30" minSize="24"><Group className="h-full" orientation="vertical"><Panel defaultSize="40" minSize="20"><aside className="h-full overflow-hidden border-b bg-card"><DocumentList activeDocumentId={documentId} onBeforeNavigate={stopPlayback} /></aside></Panel><Separator className="h-px bg-border hover:bg-primary/50" /><Panel defaultSize="60" minSize="20"><AnalysisTabsPanel activeSceneNumber={activeSceneNumber} document={document} onDraftChange={!isPlaybackActive ? updateDraft : undefined} onOpenSimilarities={() => setSimilarityOpen(true)} onReanalyze={!activeDraftState?.importTaskId && !selectedVersionId && !isDirty && document.capabilities.can_edit && !isPlaybackActive ? () => setReanalyzeOpen(true) : undefined} onSceneSelect={(sceneNumber) => { stopPlayback(); setActiveSceneNumber(sceneNumber); setSceneJumpVersion((version) => version + 1); setPlaybackStart({ documentId: document.document_id, sceneNumber, utteranceIndex: 0 }) }} onSelectVersion={selectHistoryVersion} titleMode={document.capabilities.can_edit && !isPlaybackActive ? 'editable' : 'readonly'} versions={versionsQuery.data?.items ?? []} /></Panel></Group></Panel><Separator className="w-px bg-border hover:bg-primary/50" /><Panel defaultSize="47" minSize="30"><EditorPane activeScene={selectedScene} editable={Boolean(document.capabilities.can_edit && !isPlaybackActive)} isSaving={saveMutation.isPending} onChange={(source_fountain) => updateDraft({ source_fountain })} onCursorPositionChange={(position) => setPlaybackStart(position ? { ...position, documentId: document.document_id } : undefined)} onSave={requestSave} playbackPosition={isPlaybackActive ? playbackPosition : undefined} player={<AudiobookPlayer documentId={document.document_id} draft={activeDraftState?.importTaskId ? { source_fountain: document.source_fountain, voice_assignments: document.voice_assignments ?? [] } : undefined} isPlaying={isPlaybackActive} onBeforePlay={() => { const missing = missingVoiceSpeakers(document); if (missing.length > 0) { setMissingVoices(missing); return false } return true }} onPlaybackPositionChange={setPlaybackPosition} onPlayingChange={setIsPlaybackActive} onSceneJump={(sceneNumber) => { stopPlayback(); setActiveSceneNumber(sceneNumber); setSceneJumpVersion((version) => version + 1); setPlaybackStart({ documentId: document.document_id, sceneNumber, utteranceIndex: 0 }) }} playbackStart={playbackStart?.documentId === document.document_id ? playbackStart : undefined} scenes={document.scenes} stopSignal={playbackStopVersion} />} saveEnabled={Boolean(document.capabilities.can_edit && isDirty && !isPlaybackActive)} sceneJumpVersion={sceneJumpVersion} value={document.source_fountain} /></Panel><Separator className="w-px bg-border hover:bg-primary/50" /><Panel defaultSize="23" minSize="18"><aside className="h-full overflow-hidden bg-card"><AnalysisContent document={document} isDraft={Boolean(activeDraftState?.importTaskId)} onDraftChange={!isPlaybackActive ? updateDraft : undefined} titleMode={document.capabilities.can_edit && !isPlaybackActive ? 'editable' : 'readonly'} /></aside></Panel></Group>
    <ValenceSimilarityDialog items={similaritiesQuery.data?.items.map((item) => ({ label: item.name, value: item.percentage.toFixed(2) })) ?? []} onOpenChange={setSimilarityOpen} open={similarityOpen} />
    <ReanalyzeDialog isPending={reanalyzeMutation.isPending} onConfirm={() => reanalyzeMutation.mutate()} onOpenChange={setReanalyzeOpen} open={reanalyzeOpen} />
    <ImportTitleDialog isPending={saveMutation.isPending} onConfirm={confirmImportSave} onOpenChange={setImportTitleOpen} onTitleChange={setImportTitle} open={importTitleOpen} title={importTitle} />
    <AlertDialog onOpenChange={setHistoryConfirmOpen} open={historyConfirmOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle><AlertDialogDescription>Choose a history version only after discarding the current unsaved draft.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => { void discardAndSwitchHistory() }}>Discard and switch</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog onOpenChange={setDeleteOpen} open={deleteOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t('analysis:dialogs.deleteTitle')}</AlertDialogTitle><AlertDialogDescription>{t('analysis:dialogs.deleteDescription', { title: document.title })}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>{t('common:actions.cancel')}</AlertDialogCancel><AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>{t('common:actions.delete')}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <ExportDialog document={document} onOpenChange={setExportOpen} open={exportOpen} />
    <TaskProgressDialog now={progressClock} onCancel={cancelActiveTask} onDismiss={dismissTaskProgress} task={taskProgress} />
    <RuntimeErrorDialog error={runtimeError} onDismiss={() => setRuntimeError(undefined)} />
    <MissingVoicesDialog onDismiss={() => setMissingVoices(undefined)} speakers={missingVoices} />
  </main>
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

export function EmptyAnalysis({ onImport }: { onImport: (file: File | undefined) => Promise<void> }) {
  const { t } = useTranslation(['analysis', 'common', 'importExport'])
  const fileInputRef = useRef<HTMLInputElement>(null)
  return <main className="flex h-svh min-w-[1180px] flex-col overflow-hidden bg-background"><NarravantHeader actions={<><IconAction label={t('analysis:dialogs.importDocument')} onClick={() => fileInputRef.current?.click()}><FileInput /></IconAction><IconAction disabled label={t('importExport:export.title')}><FileOutput /></IconAction><IconAction disabled destructive label={t('common:actions.delete')}><Trash2 /></IconAction></>} /><Group className="flex-1" orientation="horizontal"><Panel defaultSize="30" minSize="24"><Group className="h-full" orientation="vertical"><Panel defaultSize="40" minSize="20"><aside className="h-full overflow-hidden border-b bg-card"><DocumentList /></aside></Panel><Separator className="h-px bg-border" /><Panel defaultSize="60" minSize="20"><section className="grid h-full place-items-center bg-card p-6 text-center text-sm text-muted-foreground">{t('importExport:import.begin')}</section></Panel></Group></Panel><Separator className="w-px bg-border" /><Panel defaultSize="47" minSize="30"><section className="grid h-full place-items-center bg-background p-7 text-center text-sm text-muted-foreground"><input accept={IMPORT_FILE_ACCEPT} className="hidden" onChange={(event) => { void onImport(event.target.files?.[0]); event.target.value = '' }} ref={fileInputRef} type="file" /><div><FileInput className="mx-auto mb-3 size-6 text-primary" /><p>{IMPORT_FILE_DESCRIPTION}</p></div></section></Panel><Separator className="w-px bg-border" /><Panel defaultSize="23" minSize="18"><aside className="grid h-full place-items-center bg-card p-6 text-center text-sm text-muted-foreground"><div><Sparkles className="mx-auto mb-3 size-7 text-primary" /><p>{t('importExport:import.begin')}</p></div></aside></Panel></Group></main>
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

function EditorPane({ activeScene, editable, isSaving, onChange, onCursorPositionChange, onSave, playbackPosition, player, saveEnabled, sceneJumpVersion, value }: { activeScene?: Scene; editable: boolean; isSaving: boolean; onChange: (text: string) => void; onCursorPositionChange: (position: PlaybackStartPosition | undefined) => void; onSave: () => void; playbackPosition?: PlaybackStartPosition; player?: React.ReactNode; saveEnabled: boolean; sceneJumpVersion: number; value: string }) {
  const { t } = useTranslation(['analysis', 'common'])
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const valueRef = useRef(value)
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
    if (!playbackPosition || !element) return
    const range = utteranceRange(source, playbackPosition.sceneNumber, playbackPosition.utteranceIndex)
    if (!range) return
    element.focus({ preventScroll: true })
    element.setSelectionRange(range.start, range.end)
    scrollTextRangeIntoView(element, source, range)
  }, [playbackPosition])
  const updateCursorPosition = (source: string, cursorOffset: number) => {
    if (!editable) return
    onCursorPositionChange(playbackStartAtOffset(source, cursorOffset))
  }
  return <section className="flex h-full flex-col">{player && <div className="border-b p-2">{player}</div>}<Textarea aria-label={t('analysis:editor.scriptBody')} className="min-h-0 flex-1 resize-none rounded-none border-0 bg-background p-7 font-mono text-sm leading-7 shadow-none focus-visible:ring-0" onChange={(event) => { onChange(event.target.value); updateCursorPosition(event.target.value, event.currentTarget.selectionStart) }} onClick={(event) => updateCursorPosition(value, event.currentTarget.selectionStart)} onKeyDown={(event) => { if (!editable) event.preventDefault() }} onKeyUp={(event) => updateCursorPosition(value, event.currentTarget.selectionStart)} onMouseDown={(event) => { if (!editable) event.preventDefault() }} onSelect={(event) => updateCursorPosition(value, event.currentTarget.selectionStart)} readOnly={!editable} ref={textAreaRef} value={value} /><div className="grid shrink-0 grid-cols-3 items-center border-t bg-card p-3"><span className="text-xs text-muted-foreground">{t('analysis:sceneCount', { count: countSceneHeadings(value) })}</span><Button className="justify-self-center" disabled={!saveEnabled || isSaving} onClick={onSave} size="sm" variant="outline"><Save />{isSaving ? t('analysis:editor.saving') : t('common:actions.save')}</Button></div></section>
}
function ReanalyzeDialog({ isPending, onConfirm, onOpenChange, open }: { isPending: boolean; onConfirm: () => void; onOpenChange: (open: boolean) => void; open: boolean }) {
  return <Dialog onOpenChange={onOpenChange} open={open}><DialogContent><DialogHeader><DialogTitle>Reanalyze Emotional Arc</DialogTitle></DialogHeader><DialogFooter><Button onClick={() => onOpenChange(false)} variant="outline">Cancel</Button><Button disabled={isPending} onClick={onConfirm}>{isPending ? 'Starting…' : 'Reanalyze'}</Button></DialogFooter></DialogContent></Dialog>
}
