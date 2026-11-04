import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiClientError } from '@/api/client'
import { discardImportDraft, importDocument } from '@/api/documents'
import { cancelTask, streamTaskProgress, type TaskProgressCursor } from '@/api/tasks'
import { createImportSession, isImportTerminal, reduceImportSession, type ImportSession, type ImportSessionAction, type ImportSessionActions } from './import-session'

const ImportContext = createContext<{ session: ImportSession; actions: ImportSessionActions } | undefined>(undefined)
export function ImportSessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState(createImportSession)
  // 同じイベント内の開始・consumeも直列化する。本文の編集ストアは持たない。
  const state = useRef(session)
  const generation = useRef(0)
  const controller = useRef<AbortController | undefined>(undefined)
  const cursor = useRef<TaskProgressCursor>({ deliveredEventIds: new Set() })
  const cancelInFlight = useRef(new Set<string>())
  const mounted = useRef(true)
  const dispatch = useCallback((action: ImportSessionAction) => {
    if (!mounted.current) return
    state.current = reduceImportSession(state.current, action)
    setSession(state.current)
  }, [])
  useEffect(() => {
    mounted.current = true
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- 最新の購読をunmount時に中断する。
    return () => { mounted.current = false; generation.current++; controller.current?.abort() }
  }, [])
  const subscribe = useCallback(async (operationId: string, taskId: string) => {
    controller.current?.abort()
    const active = ++generation.current
    const abort = new AbortController()
    controller.current = abort
    const deliver = (action: ImportSessionAction) => { if (active === generation.current && !abort.signal.aborted) dispatch(action) }
    try {
      await streamTaskProgress(taskId, {
        onProgress: (progress) => deliver({ type: 'progress', operationId, progress }),
        onCompleted: (event) => deliver({ type: 'completed', operationId, event, finishedAt: Date.now() }),
        onError: (event) => deliver({ type: 'taskFailed', operationId, failure: { kind: 'task', event }, finishedAt: Date.now() }),
        onCancelled: () => deliver({ type: 'cancelled', operationId, finishedAt: Date.now() }),
      }, undefined, abort.signal, cursor.current)
    } catch (error) {
      deliver({ type: 'disconnected', operationId, failure: { kind: 'transport', code: error instanceof ApiClientError ? error.code : 'SSE_DISCONNECTED' } })
    }
  }, [dispatch])
  const sendCancel = useCallback(async (operationId: string, taskId: string) => {
    if (state.current.operationId !== operationId || cancelInFlight.current.has(operationId) || isImportTerminal(state.current)) return
    cancelInFlight.current.add(operationId)
    try { await cancelTask(taskId) } catch (error) {
      // 409は追跡中のSSEが確定した終端を採用する。
      if (!(error instanceof ApiClientError && error.status === 409)) dispatch({ type: 'cancelRejected', operationId })
    } finally { cancelInFlight.current.delete(operationId) }
  }, [dispatch])
  const actions = useMemo<ImportSessionActions>(() => ({
    async start(file) {
      if (state.current.phase !== 'idle') return
      const operationId = crypto.randomUUID()
      dispatch({ type: 'start', operationId, startedAt: Date.now() })
      cursor.current = { deliveredEventIds: new Set() }
      try {
        const accepted = await importDocument(file)
        if (!mounted.current || state.current.operationId !== operationId) return
        dispatch({ type: 'accepted', operationId, taskId: accepted.task_id })
        void subscribe(operationId, accepted.task_id)
        if (state.current.cancelIntent) await sendCancel(operationId, accepted.task_id)
      } catch (error) {
        const knownRejected = error instanceof ApiClientError && error.status >= 400 && error.status < 500
        const failure = { kind: 'start' as const, code: error instanceof ApiClientError ? error.code : 'IMPORT_FAILED' }
        if (knownRejected) dispatch({ type: 'taskFailed', operationId, failure, finishedAt: Date.now() })
        else dispatch({ type: 'disconnected', operationId, failure })
      }
    },
    show: () => dispatch({ type: 'show' }),
    hide: () => dispatch({ type: 'hide' }),
    askCancel: () => dispatch({ type: 'askCancel' }),
    dismissCancel: () => dispatch({ type: 'dismissCancel' }),
    async confirmCancel() {
      const current = state.current
      if (!current.operationId || !current.cancelConfirmationOpen || current.cancelIntent || isImportTerminal(current)) return
      dispatch({ type: 'cancelIntent', operationId: current.operationId })
      if (current.taskId) await sendCancel(current.operationId, current.taskId)
    },
    async reconnect() {
      const current = state.current
      if (current.phase !== 'unknown' || !current.taskId || !current.operationId) return
      dispatch({ type: 'reconnect', operationId: current.operationId })
      await subscribe(current.operationId, current.taskId)
    },
    consumeResult(expectedOperationId) {
      const current = state.current
      if (current.operationId !== expectedOperationId || current.phase !== 'completed' || !current.pendingResult || !current.taskId || current.discardPending) return
      const result = { operationId: expectedOperationId, taskId: current.taskId, draft: current.pendingResult }
      dispatch({ type: 'consume', operationId: expectedOperationId })
      generation.current++; controller.current?.abort()
      return result
    },
    async discardResult(expectedOperationId) {
      const current = state.current
      if (current.operationId !== expectedOperationId || current.phase !== 'completed' || !current.taskId || current.discardPending) return false
      dispatch({ type: 'discardStarted', operationId: expectedOperationId })
      let succeeded = false
      try { await discardImportDraft(current.taskId); succeeded = true } catch (error) { succeeded = error instanceof ApiClientError && error.status === 404 }
      dispatch({ type: 'discardFinished', operationId: expectedOperationId, succeeded })
      return succeeded
    },
    acknowledgeTerminal() { if (state.current.operationId) dispatch({ type: 'acknowledge', operationId: state.current.operationId }) },
  }), [dispatch, sendCancel, subscribe])
  return <ImportContext.Provider value={{ session, actions }}>{children}</ImportContext.Provider>
}
// oxlint-disable-next-line react/only-export-components -- Providerとhookの公開場所はSpecの内部契約。
export function useImportSession() {
  const value = useContext(ImportContext)
  if (!value) throw new Error('ImportSessionProvider is required.')
  return value
}
