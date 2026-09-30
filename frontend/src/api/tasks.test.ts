import { describe, expect, it, vi } from 'vitest'

import { taskCompletedEventSchema, taskProgressEventSchema } from './generated/task-events'
import { cancelTask, streamTaskProgress } from './tasks'

describe('streamTaskProgress', () => {
  it('OpenAPIから生成したSSE schemaがprogress payloadを検証する', () => {
    expect(taskProgressEventSchema.parse({ phase: 'analyzing', percentage: 42, message: '解析中', received_characters: 128 })).toEqual({
      phase: 'analyzing',
      percentage: 42,
      message: '解析中',
      received_characters: 128,
    })
    expect(() => taskProgressEventSchema.parse({ phase: 'analyzing', progress: 42, message: '解析中' })).toThrow()
  })

  it('SSEの進捗イベントをUI用の値へ変換する', async () => {
    const encoder = new TextEncoder()
    const response = new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'event: progress\ndata: {"phase":"analyzing","percentage":42,"message":"解析中","received_characters":128}\n\nevent: completed\ndata: {"document_id":"doc-1","version_id":1}\n\n',
          ),
        )
        controller.close()
      },
    }))
    const onProgress = vi.fn()

    await streamTaskProgress('task-1', { onProgress }, vi.fn().mockResolvedValue(response))

    expect(onProgress).toHaveBeenCalledWith({ phase: 'analyzing', percentage: 42, message: '解析中', received_characters: 128 })
  })

  it('再分析完了SSEの未保存arc draftをOpenAPI schemaで検証して渡す', async () => {
    const encoder = new TextEncoder()
    const response = new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          'event: completed\ndata: {"document_id":"doc-1","version_id":1,"reanalysis":{"emotion_arc":{"valence":[4],"tension":[0],"characters":{"A":[7]},"scene_mapping":[{"point_number":1,"start_scene_number":1,"end_scene_number":1,"representative_scene_number":1}],"valence_vector":[0,0,0,0,0,0,0,0,0,1]}}}\n\n',
        ))
        controller.close()
      },
    }))
    const onCompleted = vi.fn()

    await streamTaskProgress('task-1', { onCompleted }, vi.fn().mockResolvedValue(response))

    expect(onCompleted).toHaveBeenCalledWith(expect.objectContaining({
      reanalysis: expect.objectContaining({ emotion_arc: expect.objectContaining({ valence: [4], tension: [0] }) }),
    }))
    expect(() => taskCompletedEventSchema.parse({ reanalysis: { emotion_arc: { valence: 'bad' } } })).toThrow()
  })

  it('terminal eventなしの切断をSSE_DISCONNECTEDとして返す', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockImplementation(async () => (
      new Response(new ReadableStream({ start: (controller) => controller.close() }))
    ))
    const result = streamTaskProgress('task-1', {}, fetcher)
    const assertion = expect(result).rejects.toMatchObject({ code: 'SSE_DISCONNECTED' })
    await vi.runAllTimersAsync()

    await assertion
    vi.useRealTimers()
  })

  it('明示的なキャンセル要求をAPI契約で送信する', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ task_id: 'task-1', status: 'cancel_requested' }), { status: 202 }),
    )

    await expect(cancelTask('task-1', fetcher)).resolves.toEqual({ task_id: 'task-1', status: 'cancel_requested' })
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/tasks/task-1/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('non-terminal切断後にfresh tokenとLast-Event-IDで再接続しreplayを重複表示しない', async () => {
    const encoder = new TextEncoder()
    const response = (frames: string) => new Response(new ReadableStream({
      start(controller) { controller.enqueue(encoder.encode(frames)); controller.close() },
    }))
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response('id: 1\nevent: progress\ndata: {"phase":"reading","percentage":10,"message":"A"}\n\n'))
      .mockResolvedValueOnce(response('id: 1\nevent: progress\ndata: {"phase":"reading","percentage":10,"message":"A"}\n\nid: 2\nevent: completed\ndata: {"document_id":"doc-1","version_id":1}\n\n'))
    const onProgress = vi.fn()

    await streamTaskProgress('task-1', { onProgress }, fetcher)

    expect(fetcher.mock.calls[1][1].headers).toMatchObject({ 'Last-Event-ID': '1' })
    expect(onProgress).toHaveBeenCalledTimes(1)
  })

  it('データ受信がある再接続ではretryCountをリセットし、TASK_SSE_MAX_RETRIES以上の接続回数でも完走する', async () => {
    const encoder = new TextEncoder()
    const response = (frames: string) => new Response(new ReadableStream({
      start(controller) { controller.enqueue(encoder.encode(frames)); controller.close() },
    }))
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response('id: 1\nevent: progress\ndata: {"phase":"analyzing","percentage":10,"message":"P1"}\n\n'))
      .mockResolvedValueOnce(response('id: 2\nevent: progress\ndata: {"phase":"analyzing","percentage":20,"message":"P2"}\n\n'))
      .mockResolvedValueOnce(response('id: 3\nevent: progress\ndata: {"phase":"analyzing","percentage":30,"message":"P3"}\n\n'))
      .mockResolvedValueOnce(response('id: 4\nevent: progress\ndata: {"phase":"analyzing","percentage":40,"message":"P4"}\n\n'))
      .mockResolvedValueOnce(response('id: 5\nevent: completed\ndata: {"document_id":"doc-1","version_id":1}\n\n'))
    const onProgress = vi.fn()
    const onCompleted = vi.fn()

    await streamTaskProgress('task-1', { onProgress, onCompleted }, fetcher)

    expect(fetcher).toHaveBeenCalledTimes(5)
    expect(onProgress).toHaveBeenCalledTimes(4)
    expect(onCompleted).toHaveBeenCalledWith({ document_id: 'doc-1', version_id: 1 })
  })
})
