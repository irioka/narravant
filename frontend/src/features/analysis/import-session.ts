import type { TaskProgress, TaskStreamCallbacks } from '@/api/tasks'

type CompletedEvent = Parameters<NonNullable<TaskStreamCallbacks['onCompleted']>>[0]
type TaskErrorEvent = Parameters<NonNullable<TaskStreamCallbacks['onError']>>[0]
export type ImportDraftPayload = NonNullable<CompletedEvent['import_draft']>
export type ImportPhase = 'idle' | 'starting' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled' | 'unknown'
export type ImportFailure = { kind: 'task'; event: TaskErrorEvent } | { kind: 'transport' | 'start'; code: string }
export interface ImportSession {
  operationId: string | null
  taskId?: string
  phase: ImportPhase
  progress?: TaskProgress
  pendingResult?: ImportDraftPayload
  failure?: ImportFailure
  actionError?: 'cancel_failed' | 'discard_failed'
  startedAt?: number
  finishedAt?: number
  dialogOpen: boolean
  cancelConfirmationOpen: boolean
  cancelIntent: boolean
  discardPending: boolean
}
export interface PendingImport { operationId: string; taskId: string; draft: ImportDraftPayload }
export interface ImportSessionActions {
  start(file: File): Promise<void>
  show(): void
  hide(): void
  askCancel(): void
  dismissCancel(): void
  confirmCancel(): Promise<void>
  reconnect(): Promise<void>
  consumeResult(expectedOperationId: string): PendingImport | undefined
  discardResult(expectedOperationId: string): Promise<boolean>
  acknowledgeTerminal(): void
}
export type ImportSessionAction =
  | { type: 'start'; operationId: string; startedAt: number }
  | { type: 'accepted'; operationId: string; taskId: string }
  | { type: 'progress'; operationId: string; progress: TaskProgress }
  | { type: 'completed'; operationId: string; event: CompletedEvent; finishedAt: number }
  | { type: 'taskFailed'; operationId: string; failure: ImportFailure; finishedAt: number }
  | { type: 'disconnected'; operationId: string; failure: ImportFailure }
  | { type: 'cancelled'; operationId: string; finishedAt: number }
  | { type: 'cancelIntent' | 'cancelRejected' | 'reconnect' | 'consume' | 'discardStarted' | 'acknowledge'; operationId: string }
  | { type: 'discardFinished'; operationId: string; succeeded: boolean }
  | { type: 'show' | 'hide' | 'askCancel' | 'dismissCancel' }
export function createImportSession(): ImportSession {
  return { operationId: null, phase: 'idle', dialogOpen: false, cancelConfirmationOpen: false, cancelIntent: false, discardPending: false }
}
export function isImportTerminal(state: ImportSession): boolean { return ['completed', 'failed', 'cancelled'].includes(state.phase) }
export function reduceImportSession(state: ImportSession, action: ImportSessionAction): ImportSession {
  if (action.type === 'start') return state.phase === 'idle' ? { ...createImportSession(), operationId: action.operationId, phase: 'starting', startedAt: action.startedAt, dialogOpen: true } : state
  if ('operationId' in action && action.operationId !== state.operationId) return state
  switch (action.type) {
    case 'show': return { ...state, dialogOpen: true }
    case 'hide': return { ...state, dialogOpen: false, cancelConfirmationOpen: false }
    case 'askCancel': return !isImportTerminal(state) && state.phase !== 'idle' && !state.cancelIntent ? { ...state, cancelConfirmationOpen: true } : state
    case 'dismissCancel': return { ...state, cancelConfirmationOpen: false }
    case 'consume': return state.phase === 'completed' && !state.discardPending ? createImportSession() : state
    case 'discardStarted': return state.phase === 'completed' ? { ...state, discardPending: true, actionError: undefined } : state
    case 'discardFinished': return state.discardPending ? (action.succeeded ? createImportSession() : { ...state, discardPending: false, actionError: 'discard_failed' }) : state
    case 'acknowledge': return state.phase === 'failed' || state.phase === 'cancelled' ? createImportSession() : state
  }
  if (isImportTerminal(state) || state.phase === 'idle') return state
  switch (action.type) {
    case 'accepted': return { ...state, taskId: action.taskId, phase: state.cancelIntent ? 'cancelling' : 'running' }
    case 'progress': return { ...state, progress: action.progress }
    case 'completed': return action.event.import_draft
      ? { ...state, phase: 'completed', finishedAt: action.finishedAt, pendingResult: action.event.import_draft, failure: undefined, actionError: undefined, cancelConfirmationOpen: false }
      : { ...state, phase: 'failed', finishedAt: action.finishedAt, failure: { kind: 'start', code: 'IMPORT_FAILED' }, cancelConfirmationOpen: false }
    case 'taskFailed': return { ...state, phase: 'failed', finishedAt: action.finishedAt, failure: action.failure, cancelConfirmationOpen: false }
    case 'cancelled': return { ...state, phase: 'cancelled', finishedAt: action.finishedAt, cancelConfirmationOpen: false, failure: undefined, actionError: undefined }
    case 'cancelIntent': return { ...state, phase: state.taskId ? 'cancelling' : state.phase, cancelIntent: true, cancelConfirmationOpen: false, actionError: undefined }
    case 'cancelRejected': return { ...state, cancelIntent: false, actionError: 'cancel_failed', phase: state.phase === 'unknown' ? 'unknown' : 'running' }
    case 'disconnected': return { ...state, phase: 'unknown', failure: action.failure }
    case 'reconnect': return { ...state, phase: state.cancelIntent ? 'cancelling' : 'running', failure: undefined }
  }
}
