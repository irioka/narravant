import { describe, expect, it } from 'vitest'

import { parseSseChunks } from './sse'

describe('parseSseChunks', () => {
  it('分割されたSSEイベントを復元する', () => {
    const events = parseSseChunks([
      'event: progress\ndata: {"task_id":"task-1",',
      '"message":"解析中"}\n\n',
      'event: heartbeat\ndata: {"task_id":"task-1"}\n\n',
    ])

    expect(events).toEqual([
      { event: 'progress', data: { task_id: 'task-1', message: '解析中' } },
      { event: 'heartbeat', data: { task_id: 'task-1' } },
    ])
  })

  it('dataが複数行の場合は改行で結合する', () => {
    const events = parseSseChunks([
      'event: delta\ndata: {"text":"前半"}\ndata: {"text":"後半"}\n\n',
    ])

    expect(events).toEqual([
      { event: 'delta', data: [{ text: '前半' }, { text: '後半' }] },
    ])
  })

  it('CRLFとcomment heartbeatを処理し、終端separatorなしのframeも復元する', () => {
    const events = parseSseChunks([
      ': heartbeat\r\n\r\nid: 7\r\nevent: completed\r\ndata: {"document_id":"doc-1"}',
    ])

    expect(events).toEqual([{ id: '7', event: 'completed', data: { document_id: 'doc-1' } }])
  })
})
