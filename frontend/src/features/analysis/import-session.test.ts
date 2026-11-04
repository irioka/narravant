import { describe, expect, it } from 'vitest'
import { createImportSession, reduceImportSession } from './import-session'

describe('Import session', () => {
  it('閉じても追跡し、別operationと終端後の進捗を無視する', () => {
    let state = reduceImportSession(createImportSession(), { type: 'start', operationId: 'op-1', startedAt: 0 })
    state = reduceImportSession(state, { type: 'accepted', operationId: 'op-1', taskId: 'task-1' })
    state = reduceImportSession(state, { type: 'hide' })
    const progress = { phase: 'analyzing' as const, percentage: 42, message: '合成進捗' }
    state = reduceImportSession(state, { type: 'progress', operationId: 'op-1', progress })
    expect(state).toMatchObject({ taskId: 'task-1', phase: 'running', dialogOpen: false, progress })
    expect(reduceImportSession(state, { type: 'progress', operationId: 'old', progress })).toEqual(state)
    state = reduceImportSession(state, { type: 'cancelIntent', operationId: 'op-1' })
    expect(reduceImportSession(state, { type: 'progress', operationId: 'op-1', progress }).phase).toBe('cancelling')
    state = reduceImportSession(state, { type: 'cancelled', operationId: 'op-1', finishedAt: 65_000 })
    expect(reduceImportSession(state, { type: 'disconnected', operationId: 'op-1', failure: { kind: 'transport', code: 'SSE_DISCONNECTED' } })).toEqual(state)
  })
  it('unknownは開始枠を保持し、acknowledgeでは解除しない', () => {
    let state = reduceImportSession(createImportSession(), { type: 'start', operationId: 'op', startedAt: 0 })
    state = reduceImportSession(state, { type: 'disconnected', operationId: 'op', failure: { kind: 'start', code: 'IMPORT_FAILED' } })
    expect(reduceImportSession(state, { type: 'acknowledge', operationId: 'op' })).toEqual(state)
    expect(reduceImportSession(state, { type: 'start', operationId: 'new', startedAt: 1 })).toEqual(state)
  })
})
