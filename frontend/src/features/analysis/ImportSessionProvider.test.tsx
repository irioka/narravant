import { StrictMode, useEffect } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { importDocument, discardImportDraft } from '@/api/documents'
import { cancelTask, streamTaskProgress, type TaskStreamCallbacks } from '@/api/tasks'
import { ImportSessionProvider, useImportSession } from './ImportSessionProvider'
vi.mock('@/api/documents', () => ({ importDocument: vi.fn(), discardImportDraft: vi.fn() }))
vi.mock('@/api/tasks', () => ({ streamTaskProgress: vi.fn(), cancelTask: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const file = new File(['synthetic'], 'synthetic.txt')
function Controls() {
  const { session, actions } = useImportSession()
  return <><output>{session.phase}:{session.taskId}:{String(session.dialogOpen)}</output><button onClick={() => { void actions.start(file); void actions.start(file) }}>start</button><button onClick={actions.hide}>hide</button><button onClick={actions.show}>show</button><button onClick={actions.askCancel}>ask</button><button onClick={() => { void actions.confirmCancel(); void actions.confirmCancel() }}>cancel</button><button onClick={() => void actions.reconnect()}>reconnect</button></>
}
it('childrenの再mount・StrictModeでも開始1回、購読を継続する', async () => {
  let callbacks!: TaskStreamCallbacks
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'task-1', task_type: 'document_import', status: 'queued' })
  vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
  const view = render(<StrictMode><ImportSessionProvider><Controls /></ImportSessionProvider></StrictMode>)
  fireEvent.click(screen.getByText('start'))
  await screen.findByText('running:task-1:true')
  fireEvent.click(screen.getByText('hide'))
  view.rerender(<StrictMode><ImportSessionProvider>{null}</ImportSessionProvider></StrictMode>)
  view.rerender(<StrictMode><ImportSessionProvider><Controls /></ImportSessionProvider></StrictMode>)
  expect(screen.getByText('running:task-1:false')).toBeInTheDocument()
  act(() => callbacks.onCancelled?.({ task_id: 'task-1' } as never))
  expect(screen.getByText('cancelled:task-1:false')).toBeInTheDocument()
  expect(importDocument).toHaveBeenCalledTimes(1)
  expect(streamTaskProgress).toHaveBeenCalledTimes(1)
  expect(cancelTask).not.toHaveBeenCalled()
  expect(discardImportDraft).not.toHaveBeenCalled()
})
it('ID待ちの中止を受領後1回だけ送信し202では終端にしない', async () => {
  let accept!: (value: Awaited<ReturnType<typeof importDocument>>) => void
  vi.mocked(importDocument).mockImplementation(() => new Promise((resolve) => { accept = resolve }))
  vi.mocked(streamTaskProgress).mockImplementation(() => new Promise(() => {}))
  vi.mocked(cancelTask).mockResolvedValue({ task_id: 'task-1', status: 'cancel_requested' })
  render(<ImportSessionProvider><Controls /></ImportSessionProvider>)
  fireEvent.click(screen.getByText('start')); fireEvent.click(screen.getByText('ask')); fireEvent.click(screen.getByText('cancel'))
  expect(cancelTask).not.toHaveBeenCalled()
  await act(async () => accept({ task_id: 'task-1', task_type: 'document_import', status: 'queued' }))
  await waitFor(() => expect(cancelTask).toHaveBeenCalledExactlyOnceWith('task-1'))
  expect(screen.getByText('cancelling:task-1:true')).toBeInTheDocument()
})
it('古い購読のcatchは再接続をunknownへ戻さずcursorを引き継ぐ', async () => {
  let fail!: (error: Error) => void
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'task-1', task_type: 'document_import', status: 'queued' })
  vi.mocked(streamTaskProgress).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject })).mockImplementation(() => new Promise(() => {}))
  render(<ImportSessionProvider><Controls /></ImportSessionProvider>)
  fireEvent.click(screen.getByText('start'))
  await screen.findByText('running:task-1:true')
  await act(async () => fail(new Error('synthetic network')))
  fireEvent.click(screen.getByText('reconnect'))
  expect(screen.getByText('running:task-1:true')).toBeInTheDocument()
  expect(vi.mocked(streamTaskProgress).mock.calls[0][4]).toBe(vi.mocked(streamTaskProgress).mock.calls[1][4])
  expect(importDocument).toHaveBeenCalledTimes(1)
})
it.each([409, 503])('completedを受信後のcancel失敗%sは結果を消さずconsumeは1回だけ', async (status) => {
  const { ApiClientError } = await import('@/api/client')
  let api!: ReturnType<typeof useImportSession>
  let callbacks!: TaskStreamCallbacks
  let rejectCancel!: (error: Error) => void
  function Expose() { const value = useImportSession(); useEffect(() => { api = value }); return <Controls /> }
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'task', status: 'queued', task_type: 'document_import' })
  vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { callbacks = cb; return new Promise(() => {}) })
  vi.mocked(cancelTask).mockImplementation(() => new Promise((_resolve, reject) => { rejectCancel = reject }))
  render(<ImportSessionProvider><Expose /></ImportSessionProvider>)
  fireEvent.click(screen.getByText('start'))
  await screen.findByText('running:task:true')
  fireEvent.click(screen.getByText('ask')); fireEvent.click(screen.getByText('cancel'))
  act(() => callbacks.onCompleted?.({ import_draft: { document_id: 'synthetic-result' } } as never))
  await act(async () => rejectCancel(new ApiClientError(status, 'CONFLICT', 'synthetic')))
  expect(api.session.phase).toBe('completed')
  expect(api.session.pendingResult?.document_id).toBe('synthetic-result')
  const operationId = api.session.operationId!
  let results!: unknown[]
  act(() => { results = [api.actions.consumeResult(operationId), api.actions.consumeResult(operationId)] })
  expect(results[0]).toMatchObject({ taskId: 'task' })
  expect(results[1]).toBeUndefined()
})
it('破棄失敗では結果を保持し、404を成功とする', async () => {
  const { ApiClientError } = await import('@/api/client')
  let api!: ReturnType<typeof useImportSession>
  function Expose() { const value = useImportSession(); useEffect(() => { api = value }); return <Controls /> }
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'task', status: 'queued', task_type: 'document_import' })
  vi.mocked(streamTaskProgress).mockImplementation(async (_id, cb) => cb.onCompleted?.({ import_draft: { document_id: 'synthetic-result' } } as never))
  render(<ImportSessionProvider><Expose /></ImportSessionProvider>)
  fireEvent.click(screen.getByText('start'))
  await screen.findByText('completed:task:true')
  const op = api.session.operationId!
  vi.mocked(discardImportDraft).mockRejectedValueOnce(new Error('offline')).mockRejectedValueOnce(new ApiClientError(404, 'NOT_FOUND', 'gone'))
  await act(async () => { expect(await api.actions.discardResult(op)).toBe(false) })
  expect(api.session).toMatchObject({ phase: 'completed', actionError: 'discard_failed' })
  await act(async () => { expect(await api.actions.discardResult(op)).toBe(true) })
  expect(api.session.phase).toBe('idle')
})
it.each(['IMPORT_DRAFT_EXPIRED', 'PROCESS_RESTARTED', 'PERMISSION_DENIED'])('既存エラー%sを保持し表示用draftに適用しない', async (code) => {
  let api!: ReturnType<typeof useImportSession>
  function Expose() { const value = useImportSession(); useEffect(() => { api = value }); return <Controls /> }
  vi.mocked(importDocument).mockResolvedValue({ task_id: 'task', status: 'queued', task_type: 'document_import' })
  vi.mocked(streamTaskProgress).mockImplementation(async (_id, cb) => cb.onError?.({ code, message: 'PRIVATE', retryable: false }))
  render(<ImportSessionProvider><Expose /></ImportSessionProvider>)
  fireEvent.click(screen.getByText('start'))
  await screen.findByText('failed:task:true')
  expect(api.session.pendingResult).toBeUndefined()
  expect(api.session.failure).toMatchObject({ kind: 'task', event: { code } })
  expect(importDocument).toHaveBeenCalledTimes(1)
})
it('旧Importのcancel HTTP保留は次Importのキャンセルを抑止しない', async () => {
  let api!: ReturnType<typeof useImportSession>
  const streams: TaskStreamCallbacks[] = []
  const finishes: Array<(value: Awaited<ReturnType<typeof cancelTask>>) => void> = []
  function Expose() { const value = useImportSession(); useEffect(() => { api = value }); return <Controls /> }
  vi.mocked(importDocument).mockResolvedValueOnce({ task_id: 'A', task_type: 'document_import', status: 'queued' }).mockResolvedValueOnce({ task_id: 'B', task_type: 'document_import', status: 'queued' })
  vi.mocked(streamTaskProgress).mockImplementation((_id, cb) => { streams.push(cb); return new Promise(() => {}) })
  vi.mocked(cancelTask).mockImplementation(() => new Promise((resolve) => { finishes.push(resolve) }))
  render(<ImportSessionProvider><Expose /></ImportSessionProvider>)
  await act(async () => { await api.actions.start(file) })
  act(() => api.actions.askCancel())
  act(() => { void api.actions.confirmCancel() })
  act(() => { streams[0].onCancelled?.({ message: 'Cancelled' }); api.actions.acknowledgeTerminal() })
  await act(async () => { await api.actions.start(file) })
  act(() => api.actions.askCancel())
  act(() => { void api.actions.confirmCancel() })
  expect(cancelTask).toHaveBeenNthCalledWith(2, 'B')
  await act(async () => finishes[0]({ task_id: 'A', status: 'cancel_requested' }))
  expect(api.session).toMatchObject({ taskId: 'B', phase: 'cancelling', cancelIntent: true })
  await act(async () => finishes[1]({ task_id: 'B', status: 'cancel_requested' }))
  expect(cancelTask).toHaveBeenCalledTimes(2)
})
